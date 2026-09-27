"""Offline checks for the parts that do not need a device attached."""

from __future__ import annotations

import random

import pytest
from PIL import Image, ImageDraw

from blinkit_banners.capture import Collector, is_banner_shape, is_feed_chrome, purge_non_banners
from blinkit_banners.config import CaptureConfig, Config, DetectionConfig, Location
from blinkit_banners.detect import find_banners, find_carousels
from blinkit_banners.device import Screen
from blinkit_banners.flows import find_dismissible, safe_scroll_fraction
from blinkit_banners.hierarchy import parse
from blinkit_banners.store import ATTRIBUTE_FIELDS, Store

SCREEN_W, SCREEN_H = 1080, 2400

CAROUSEL_BOX = (40, 300, 1040, 860)
STATIC_BOX = (40, 1000, 1040, 1450)

HIERARCHY = f"""<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][{SCREEN_W},{SCREEN_H}]">
    <node class="android.widget.LinearLayout" resource-id="com.grofers.customerapp:id/header"
          bounds="[0,0][1080,200]">
      <node class="android.widget.TextView" resource-id="com.grofers.customerapp:id/location_chip"
            text="Home - DLF Phase 3" clickable="true" bounds="[24,40][600,160]"/>
    </node>
    <node class="androidx.viewpager.widget.ViewPager" resource-id="com.grofers.customerapp:id/banner_pager"
          scrollable="true" bounds="[40,300][1040,860]">
      <node class="android.widget.ImageView" resource-id="com.grofers.customerapp:id/banner_image"
            bounds="[40,300][1040,860]"/>
    </node>
    <node class="android.widget.ImageView" resource-id="com.grofers.customerapp:id/promo_strip"
          bounds="[40,1000][1040,1450]"/>
    <node class="android.widget.ImageView" resource-id="com.grofers.customerapp:id/product_thumb"
          bounds="[40,1500][340,1900]"/>
    <node class="android.widget.LinearLayout" resource-id="com.grofers.customerapp:id/bottom_nav"
          bounds="[0,2200][1080,2400]"/>
  </node>
</hierarchy>
"""


def _noise(width: int, height: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    tile = Image.frombytes("RGB", (32, 32), bytes(rng.getrandbits(8) for _ in range(32 * 32 * 3)))
    return tile.resize((width, height), Image.NEAREST)


def _screenshot() -> Image.Image:
    image = Image.new("RGB", (SCREEN_W, SCREEN_H), (250, 250, 250))
    for seed, box in ((1, CAROUSEL_BOX), (2, STATIC_BOX)):
        left, top, right, bottom = box
        image.paste(_noise(right - left, bottom - top, seed), (left, top))
    return image


@pytest.fixture
def screen() -> Screen:
    return Screen(image=_screenshot(), root=parse(HIERARCHY), scale=1.0)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        capture=CaptureConfig(output_dir=str(tmp_path), save_full_screenshots=False),
        detection=DetectionConfig(),
    )


def test_finds_both_banners_and_skips_product_thumb(screen, cfg):
    banners = find_banners(screen.root, cfg.detection)
    ids = [b.node.resource_id.rsplit("/", 1)[-1] for b in banners]
    assert ids == ["banner_image", "promo_strip"]


def test_carousel_membership(screen, cfg):
    banners = find_banners(screen.root, cfg.detection)
    by_id = {b.node.resource_id.rsplit("/", 1)[-1]: b for b in banners}
    assert by_id["banner_image"].carousel is not None
    assert by_id["promo_strip"].carousel is None

    carousels = find_carousels(screen.root, cfg.detection)
    assert [c.resource_id.rsplit("/", 1)[-1] for c in carousels] == ["banner_pager"]


def test_wrapper_and_child_collapse_to_one_candidate(screen, cfg):
    """The ViewPager and its ImageView cover identical pixels; only one should survive."""
    banners = find_banners(screen.root, cfg.detection)
    assert sum(1 for b in banners if b.rect.as_tuple() == CAROUSEL_BOX) == 1


def test_allowlist_is_additive_by_default(screen, cfg):
    """Naming one banner must not blind us to the others."""
    cfg.detection.resource_id_allow = ["promo_strip"]
    banners = find_banners(screen.root, cfg.detection)
    assert [b.node.resource_id.rsplit("/", 1)[-1] for b in banners] == ["banner_image", "promo_strip"]
    assert {b.slot_key: b.from_id for b in banners} == {"banner_image": False, "promo_strip": True}


def test_strict_ids_restricts_to_the_allowlist(screen, cfg):
    cfg.detection.resource_id_allow = ["promo_strip"]
    cfg.detection.strict_ids = True
    banners = find_banners(screen.root, cfg.detection)
    assert [b.node.resource_id.rsplit("/", 1)[-1] for b in banners] == ["promo_strip"]


def test_renamed_ids_fall_back_to_shape(screen, cfg):
    """Simulates an app update that renames every banner view."""
    cfg.detection.resource_id_allow = ["banner_v2_hero", "promo_v2"]
    banners = find_banners(screen.root, cfg.detection)
    assert [b.slot_key for b in banners] == ["banner_image", "promo_strip"]
    assert not any(b.from_id for b in banners)


def test_id_match_survives_a_shape_change(cfg):
    """A named view that no longer looks like a banner is still captured."""
    tall = HIERARCHY.replace('bounds="[40,1000][1040,1450]"', 'bounds="[40,1000][1040,2100]"')
    cfg.detection.resource_id_allow = ["promo_strip"]
    banners = find_banners(parse(tall), cfg.detection)
    assert "promo_strip" in [b.slot_key for b in banners]


OVERLAY = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.view.ViewGroup" resource-id="a:id/bottom_clip_lottie_image_view"
          bounds="[24,400][1056,830]"/>
    <node class="android.widget.ImageView" resource-id="a:id/real_banner"
          bounds="[24,1000][1056,1550]"/>
  </node>
</hierarchy>
"""


def test_empty_layout_overlay_is_not_mistaken_for_an_image(cfg):
    """Both are banner-shaped; only the ImageView holds pixels.

    Blinkit lays childless ViewGroups over the feed, and cropping one captures
    whatever renders beneath it - previously a grid of category tiles.
    """
    banners = find_banners(parse(OVERLAY), cfg.detection)
    assert [b.slot_key for b in banners] == ["real_banner"]


def test_banner_overlapped_by_bottom_overlay_is_deferred(cfg):
    """Its centre is in view but its foot is under the nav bar, so it must wait."""
    partly = HIERARCHY.replace('bounds="[40,1000][1040,1450]"', 'bounds="[40,1900][1040,2350]"')
    root = parse(partly)

    assert "promo_strip" not in [b.slot_key for b in find_banners(root, cfg.detection)]

    cfg.detection.require_fully_visible = False
    assert "promo_strip" in [b.slot_key for b in find_banners(root, cfg.detection)]


def test_scroll_is_capped_so_banners_cannot_be_skipped():
    cfg = Config()
    cfg.detection.exclude_top_ratio = 0.23
    cfg.detection.exclude_bottom_ratio = 0.10
    cfg.feed.max_banner_height_ratio = 0.30

    fraction = safe_scroll_fraction(cfg)
    assert fraction == pytest.approx(0.37)

    # The band must still show a full banner after scrolling by this much.
    band = 1 - cfg.detection.exclude_top_ratio - cfg.detection.exclude_bottom_ratio
    assert fraction + cfg.feed.max_banner_height_ratio <= band


def test_scroll_cap_never_collapses_to_zero():
    cfg = Config()
    cfg.detection.exclude_top_ratio = 0.4
    cfg.detection.exclude_bottom_ratio = 0.4
    cfg.feed.max_banner_height_ratio = 0.5
    assert safe_scroll_fraction(cfg) == 0.15


DIALOG = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" text="No sugar, no problem" bounds="[40,300][1040,400]"/>
    <node class="android.widget.FrameLayout" bounds="[100,900][980,1500]">
      <node class="android.widget.TextView"
            text="Want to be notified about your orders and amazing offers?"
            bounds="[140,940][940,1140]"/>
      <node class="android.widget.TextView" text="Enable notifications" clickable="true"
            bounds="[140,1180][940,1320]"/>
      <node class="android.widget.TextView" text="No, thanks" clickable="true"
            bounds="[140,1340][940,1480]"/>
    </node>
  </node>
</hierarchy>
"""


def test_finds_the_dismiss_control_on_an_interstitial():
    target = find_dismissible(parse(DIALOG))
    assert target is not None and target.text == "No, thanks"


def test_feed_copy_is_not_mistaken_for_a_dismiss_control(screen):
    assert find_dismissible(screen.root) is None


def test_ingest_stores_creatives_then_dedupes(screen, cfg, tmp_path):
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)
    banners = find_banners(screen.root, cfg.detection)

    first = collector.ingest(screen, banners, "gurgaon", "home")
    assert len(first) == 2
    assert all(r.is_new_creative for r in first)
    assert store.stats() == {"creatives": 2, "sightings": 2}
    assert all(tmp_path.joinpath("creatives", f"{r.phash}.png").exists() for r in first)

    # Same screen again in the same run: no new creatives, no double-counted sightings.
    second = collector.ingest(screen, banners, "gurgaon", "home")
    assert not any(r.is_new_creative for r in second)
    assert not any(r.is_new_this_run for r in second)
    assert store.stats() == {"creatives": 2, "sightings": 2}

    # A fresh run at a new location records sightings against the same creatives.
    collector.reset_run()
    collector.ingest(screen, banners, "bengaluru", "home")
    assert store.stats() == {"creatives": 2, "sightings": 4}
    store.close()


def test_ingest_records_city_and_pincode(screen, cfg, tmp_path):
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)
    collector.place = Location(name="delhi-connaught-place", city="Delhi", pincode="110001")
    banners = find_banners(screen.root, cfg.detection)
    collector.ingest(screen, banners, "delhi-connaught-place", "home")
    row = store.conn.execute("SELECT location, city, pincode FROM sightings").fetchone()
    assert tuple(row) == ("delhi-connaught-place", "Delhi", "110001")
    store.close()


def test_unloaded_placeholder_is_skipped(cfg, tmp_path):
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)
    flat = Image.new("RGB", (1000, 560), (235, 235, 235))
    assert collector.ingest_image(flat, "gurgaon", "home", 0) is None
    assert store.stats()["creatives"] == 0
    store.close()


def test_splash_and_tagline_frames_are_skipped(cfg, tmp_path):
    """Carousel slides captured while the feed is still loading: house branding
    or a lone tagline on a flat field, not a promotional creative."""
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)

    splash = Image.new("RGB", (1024, 716), (245, 196, 51))
    ImageDraw.Draw(splash).text((360, 40), "AN ETERNAL COMPANY", fill=(0, 0, 0))
    tagline = Image.new("RGB", (1024, 716), (255, 255, 255))
    ImageDraw.Draw(tagline).text((460, 24), "doorstep", fill=(180, 180, 180))

    assert collector.ingest_image(splash, "mumbai-bandra-west", "home-carousel0", 0) is None
    assert collector.ingest_image(tagline, "mumbai-bandra-west", "home-carousel0", 0) is None
    assert store.stats()["creatives"] == 0
    store.close()


def test_headers_and_product_rails_are_not_banners():
    assert not is_banner_shape(1280, 284)   # occasion header
    assert not is_banner_shape(1208, 372)   # product rail
    assert not is_banner_shape(640, 372)    # half of that rail
    assert is_banner_shape(1024, 716)       # hero
    assert is_banner_shape(586, 415)        # promo tile
    assert is_banner_shape(1208, 610)       # store promo


def test_feed_chrome_copy_is_rejected():
    assert is_feed_chrome("quentlybought", "gold +14 more +7 more Vegetables")
    assert is_feed_chrome("RAKHI", "EXPLORE ALL RAKHI Superhero")
    assert not is_feed_chrome("Keep the party going with Lay's", "Shop now Ad")


def test_purge_removes_headers_and_keeps_heroes(cfg, tmp_path):
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)
    hero = _noise(1024, 716, seed=3)
    header = _noise(1280, 250, seed=4)
    assert collector.ingest_image(hero, "gurgaon", "home-carousel0", 0) is not None
    # Shape filter blocks the header on ingest; plant one as if it slipped in earlier.
    path = tmp_path / "header.png"
    header.save(path)
    store.add_creative("ab" * 32, str(path), 1280, 250)
    values = {field: None for field in ATTRIBUTE_FIELDS}
    values.update(headline="Festive Picks", ocr_text="Festive Picks", has_ad_badge=0)
    store.save_attributes("ab" * 32, values)
    assert purge_non_banners(store, cfg.detection) == 1
    assert store.stats()["creatives"] == 1
    store.close()


# Real hashes lifted from the library before the duplicates were purged.
# Two frames of one L'Oreal spot, a frame of a different L'Oreal video, and a
# static banner. Frames of one video sat up to 88 bits apart, two different
# videos 124 apart, and the closest pair of unrelated banners 78 - which is why
# the loose threshold is only ever applied between known video frames.
INFAILLIBLE_A = "e1c11c78631eb7325aebe0041efbc0883fbad3086eeaaab3932aaccddb2aa135"
INFAILLIBLE_B = "c1831c7ee0043739bf9fc0081eefe2041ffbd3084eea27f5bdaaa1732fa82485"
OTHER_VIDEO = "a592e31906e39a619963d9a698ece91926279a668ce664ce6d93664e2e996767"
STATIC_BANNER = "db6bd54b77398009d4801659c0460e3ce3425daca692c992a6b93b7a377f19bf"


def test_video_frames_collapse_but_separate_videos_do_not(tmp_path):
    store = Store(tmp_path / "banners.db")
    store.add_creative(INFAILLIBLE_A, "a.png", 1024, 716, is_video=True)
    store.add_creative(STATIC_BANNER, "b.png", 1024, 716)

    # Another frame of the same spot folds into it.
    assert store.match(INFAILLIBLE_B, 32, animated=True, animated_distance=104) == INFAILLIBLE_A
    # A frame of a different video stays its own creative.
    assert store.match(OTHER_VIDEO, 32, animated=True, animated_distance=104) is None
    # The loose distance never applies to a still, even a badly matching one.
    assert store.match(INFAILLIBLE_B, 32) is None
    store.close()


def test_a_still_never_folds_into_a_video(tmp_path):
    """The loose threshold must not reach across from video frames to banners."""
    store = Store(tmp_path / "banners.db")
    store.add_creative(INFAILLIBLE_A, "a.png", 1024, 716, is_video=True)

    assert store.match(STATIC_BANNER, 32, animated=True, animated_distance=104) is None
    store.close()


def test_jpeg_noise_does_not_create_a_duplicate(cfg, tmp_path):
    """Re-encoding shifts a few hash bits; dedupe_distance should absorb that."""
    store = Store(tmp_path / "banners.db")
    collector = Collector(cfg, store)
    original = _noise(1000, 560, seed=7)

    collector.ingest_image(original, "gurgaon", "home", 0)
    jpeg_path = tmp_path / "roundtrip.jpg"
    original.save(jpeg_path, quality=70)
    collector.reset_run()
    result = collector.ingest_image(Image.open(jpeg_path).convert("RGB"), "gurgaon", "home", 0)

    assert result is not None and not result.is_new_creative
    assert store.stats()["creatives"] == 1
    store.close()
