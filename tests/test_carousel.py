"""Carousel exhaustion against a simulated pager.

The real device is slow and non-deterministic, so the rotation is faked here:
a ring of distinct slides that advances on swipe, optionally also advancing on
its own to mimic the auto-rotate timer.
"""

from __future__ import annotations

import random

import pytest
from PIL import Image, ImageDraw

from blinkit_banners.capture import Collector
from blinkit_banners.config import CaptureConfig, Config, DetectionConfig, FeedConfig
from blinkit_banners.device import Screen
from blinkit_banners.flows import exhaust_carousel
from blinkit_banners.hierarchy import parse
from blinkit_banners.humanize import Human, HumanConfig
from blinkit_banners.store import Store

SCREEN_W, SCREEN_H = 1280, 2856
CAROUSEL_BOX = (0, 900, 1280, 1616)
SLIDE_BOX = (128, 900, 1152, 1616)

HIERARCHY = f"""<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][{SCREEN_W},{SCREEN_H}]">
    <node class="androidx.viewpager.widget.ViewPager" resource-id="a:id/view_pager_2"
          scrollable="true" bounds="[0,900][1280,1616]">
      <node class="android.widget.ImageView" resource-id="a:id/image_view"
            content-desc="Promotional banner" bounds="[128,900][1152,1616]"/>
    </node>
  </node>
</hierarchy>
"""


def _slide_image(seed: int) -> Image.Image:
    rng = random.Random(seed)
    tile = Image.frombytes("RGB", (32, 32), bytes(rng.getrandbits(8) for _ in range(32 * 32 * 3)))
    return tile.resize((SCREEN_W, SCREEN_H), Image.NEAREST)


def _video_frame(step: int) -> Image.Image:
    """One frame of a video ad: a fixed backdrop with copy sliding across it.

    Frames of a real spot stay related — measured 36-88 bits apart on a live
    L'Oreal ad — unlike unrelated slides, which sit around 128. The moving
    block reproduces that band, so the dedupe thresholds are tested honestly.
    """
    frame = _slide_image(0).copy()
    left = SLIDE_BOX[0] + (step * 160) % (SLIDE_BOX[2] - SLIDE_BOX[0] - 380)
    ImageDraw.Draw(frame).rectangle(
        [left, SLIDE_BOX[1] + 150, left + 380, SLIDE_BOX[1] + 560], fill=(250, 250, 250)
    )
    return frame


class FakePager:
    """Stands in for Device. Holds a ring of slides and a rotation position."""

    def __init__(self, slides: int, auto_advance_every: int | None = None, playing: bool = False):
        self.images = [_slide_image(seed) for seed in range(slides)]
        self.position = 0
        self.auto_advance_every = auto_advance_every
        # A video slot redraws on every read without anybody swiping.
        self.playing = playing
        self.frame_seed = 0
        self.captures = 0
        self.screenshots = 0
        self.swipes = 0
        self.human = Human(HumanConfig(enabled=False, carousel_settle=(0.0, 0.0)))
        self.counts: dict[str, int] = {}

    def _frame(self) -> Image.Image:
        if self.playing:
            self.frame_seed += 1
            return _video_frame(self.frame_seed)
        return self.images[self.position % len(self.images)]

    def _maybe_auto_advance(self) -> None:
        reads = self.captures + self.screenshots
        if self.auto_advance_every and reads % self.auto_advance_every == 0:
            self.position += 1

    def capture(self) -> Screen:
        self.captures += 1
        image = self._frame()
        self._maybe_auto_advance()
        return Screen(image=image, root=parse(HIERARCHY), scale=1.0)

    def screenshot(self) -> Image.Image:
        self.screenshots += 1
        image = self._frame()
        self._maybe_auto_advance()
        return image

    def swipe_carousel(self, rect) -> None:
        self.swipes += 1
        self.position += 1


@pytest.fixture
def setup(tmp_path):
    cfg = Config(
        capture=CaptureConfig(output_dir=str(tmp_path), save_full_screenshots=False),
        detection=DetectionConfig(min_width_ratio=0.40, exclude_top_ratio=0.23),
        # The video probe is exercised on its own below; leaving it off here
        # keeps the read counts in these tests about lap logic alone.
        feed=FeedConfig(carousel_max_slides=18, carousel_stall_limit=3, animation_probe=0.0),
    )
    store = Store(tmp_path / "banners.db")
    return cfg, store, Collector(cfg, store)


def _carousel_node(cfg):
    from blinkit_banners.detect import find_carousels

    return find_carousels(parse(HIERARCHY), cfg.detection)[0]


def test_walks_one_full_lap_and_stops(setup):
    cfg, store, collector = setup
    device = FakePager(slides=6)

    sightings, new = exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    assert sightings == 6 and new == 6
    assert store.stats()["creatives"] == 6
    # Six slides read, then one more read that lands back on the first.
    assert device.swipes == 6


def test_stops_on_returning_to_the_first_slide_not_at_the_cap(setup):
    cfg, store, collector = setup
    cfg.feed.carousel_max_slides = 50
    device = FakePager(slides=4)

    exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    assert store.stats()["creatives"] == 4
    assert device.swipes == 4


def test_auto_rotation_does_not_cut_the_lap_short(setup):
    """The pager also advances by itself, so swipes and slides do not line up."""
    cfg, store, collector = setup
    device = FakePager(slides=8, auto_advance_every=3)

    exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    # Some slides get skipped by the extra advances, but the lap must not end
    # after one or two frames the way a first-repeat rule would.
    assert store.stats()["creatives"] >= 4


def test_revisiting_a_covered_carousel_is_cheap(setup):
    cfg, store, collector = setup
    node = _carousel_node(cfg)

    first = FakePager(slides=6)
    exhaust_carousel(first, collector, cfg, node, "hyd", "home", 0)
    assert first.swipes == 6

    second = FakePager(slides=6)
    sightings, new = exhaust_carousel(second, collector, cfg, node, "hyd", "home", 0)

    assert sightings == 0 and new == 0
    assert second.swipes <= 3
    assert store.stats()["creatives"] == 6


def test_screenshot_fast_path_is_used_after_the_first_frame(setup):
    """Only the first frame needs a hierarchy dump to locate the slide."""
    cfg, _, collector = setup
    device = FakePager(slides=5)

    exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    assert device.captures == 1
    assert device.screenshots == 5


def test_a_video_slot_is_archived_once_not_once_per_frame(setup):
    """The real failure: a L'Oreal video ad filed seven frames as seven creatives.

    Pixel similarity cannot catch this - two frames of one spot were further
    apart (88 bits) than two genuinely different banners (78). Only the fact
    that the slide redrew itself while nobody swiped identifies it as video.
    """
    cfg, store, collector = setup
    cfg.feed.animation_probe = 0.001
    device = FakePager(slides=6, playing=True)

    exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    assert store.stats()["creatives"] == 1
    assert store.conn.execute("SELECT is_video FROM creatives").fetchone()["is_video"] == 1


def test_a_still_carousel_is_never_mistaken_for_video(setup):
    """Probing must not flag ordinary banners, which would loosen their dedupe."""
    cfg, store, collector = setup
    cfg.feed.animation_probe = 0.001
    device = FakePager(slides=6)

    exhaust_carousel(device, collector, cfg, _carousel_node(cfg), "hyd", "home", 0)

    assert store.stats()["creatives"] == 6
    flags = [r["is_video"] for r in store.conn.execute("SELECT is_video FROM creatives")]
    assert flags == [0] * 6
