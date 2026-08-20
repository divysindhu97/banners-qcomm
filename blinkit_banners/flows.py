from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .capture import Collector
from .config import Config, Location
from .detect import find_banners, find_carousels
from .device import Device, DeviceError, crop_to
from .hierarchy import Node, Rect

# Blinkit ships UI changes often. Every selector guess lives here so there is a
# single place to correct after running `discover`.
LOCATION_CHIP_HINTS = ("location", "address", "deliver", "delivery")
CURRENT_LOCATION_TEXT = re.compile(r"current location|use my location|locate me|detect location|gps", re.IGNORECASE)
CONFIRM_TEXT = re.compile(r"confirm|continue|proceed|select this|save", re.IGNORECASE)
# Interstitials that appear on relaunch (notification opt-in, rating prompts) and
# cover the feed. Anchored so feed copy cannot match by accident.
DISMISS_TEXT = re.compile(
    r"^(no,?\s*thanks|not now|maybe later|not right now|skip|dismiss|later|don'?t allow|deny|close)$",
    re.IGNORECASE,
)


def safe_scroll_fraction(cfg: Config) -> float:
    """Largest scroll that cannot skip a banner between two captures.

    A banner is only captured once wholly inside the visible band, so the feed
    must never advance by more than the band can hold beyond the tallest banner.
    Otherwise content passes from below the band to above it unseen.
    """
    band = 1.0 - cfg.detection.exclude_top_ratio - cfg.detection.exclude_bottom_ratio
    return max(0.15, band - cfg.feed.max_banner_height_ratio)


def find_dismissible(root: Node) -> Node | None:
    """A 'No, thanks'-style control on a modal covering the feed."""
    for node in root.walk():
        label = (node.text or node.content_desc).strip()
        if label and DISMISS_TEXT.match(label) and node.bounds.area:
            return node
    return None


def dismiss_interstitial(device: Device, root: Node) -> bool:
    target = find_dismissible(root)
    if target is None:
        return False
    device.tap(target.bounds)
    time.sleep(1.5)
    return True


@dataclass
class SweepSummary:
    location: str
    screens: int
    sightings: int
    new_creatives: int
    carousel_visits: int = 0

    def line(self) -> str:
        return (
            f"{self.location}: {self.screens} screens, {self.sightings} banner sightings, "
            f"{self.new_creatives} new creatives, {self.carousel_visits} carousel visits"
        )


def sweep_home(device: Device, collector: Collector, cfg: Config, location: str) -> SweepSummary:
    """Walk the home feed top to bottom, capturing every banner-shaped view."""
    device.ensure_foreground()
    screens = sightings = new_creatives = carousel_visits = 0
    scroll_cap = safe_scroll_fraction(cfg)

    for _ in range(cfg.feed.scroll_steps):
        screen = device.capture()
        if dismiss_interstitial(device, screen.root):
            screen = device.capture()
        screens += 1
        shot = collector.save_screenshot(screen, location, "home")

        carousels = find_carousels(screen.root, cfg.detection)
        candidates = find_banners(screen.root, cfg.detection)

        static = [c for c in candidates if not _inside_any(c.rect, carousels)]
        for result in collector.ingest(screen, static, location, "home", shot):
            if result.is_new_this_run:
                sightings += 1
            if result.is_new_creative:
                new_creatives += 1

        for index, carousel in enumerate(carousels):
            carousel_visits += 1
            gained, fresh = exhaust_carousel(device, collector, cfg, carousel, location, "home", index)
            sightings += gained
            new_creatives += fresh

        device.scroll_feed(cap=scroll_cap)
        device.human.settle()

    return SweepSummary(location, screens, sightings, new_creatives, carousel_visits)


def exhaust_carousel(
    device: Device,
    collector: Collector,
    cfg: Config,
    carousel: Node,
    location: str,
    surface: str,
    slot_index: int,
) -> tuple[int, int]:
    """Swipe a carousel until it comes back round to the slide it started on.

    Two details make this harder than it looks. The carousel auto-rotates on its
    own timer, so a slow loop lets it advance underneath us, which both skips
    slides and makes a repeat look like the end of the rotation. And a full
    `capture` costs a hierarchy dump, which is most of that slowness. So the slide
    rectangle is resolved once from the hierarchy and every subsequent frame is a
    screenshot-only crop of that same rectangle.

    Termination is by cycle: stop on returning to the first slide. Staleness is
    judged run-wide rather than per visit, so re-walking a carousel already
    covered earlier in the sweep costs a couple of swipes instead of a full lap.
    """
    rect = carousel.bounds
    probe = device.capture()
    inner = [c for c in find_banners(probe.root, cfg.detection) if _inside(c.rect, rect)]
    if not inner:
        return 0, 0
    slide_rect = max(inner, key=lambda c: c.rect.area).rect

    surface_name = f"{surface}-carousel{slot_index}"
    sightings = new_creatives = 0
    first_phash: str | None = None
    stale = 0

    for step in range(cfg.feed.carousel_max_slides):
        image = probe.image if step == 0 else device.screenshot()
        try:
            crop = crop_to(image, slide_rect, probe.scale)
        except ValueError:
            break

        result = collector.ingest_image(crop, location, surface_name, slot_index)
        if result is None:
            break
        if first_phash is not None and result.phash == first_phash:
            break

        if result.is_new_this_run:
            sightings += 1
            stale = 0
        else:
            stale += 1
        if result.is_new_creative:
            new_creatives += 1
        if first_phash is None:
            first_phash = result.phash
        if stale >= cfg.feed.carousel_stall_limit:
            break

        device.swipe_carousel(rect)
        device.human.carousel_settle()

    return sightings, new_creatives


def set_location(device: Device, location: Location, mode: str) -> None:
    """Point the app at a delivery location using the configured strategy."""
    if mode == "manual":
        return
    if mode == "geo":
        if location.lat is None or location.lon is None:
            raise DeviceError(f"{location.name!r} needs lat/lon for location_mode: geo.")
        device.geo_fix(location.lat, location.lon)
        _use_current_location(device)
        return
    if mode == "ui":
        if not location.search_query:
            raise DeviceError(f"{location.name!r} needs search_query for location_mode: ui.")
        _search_for_address(device, location.search_query)
        return
    raise DeviceError(f"Unknown location_mode {mode!r}; expected geo, ui or manual.")


def _use_current_location(device: Device, timeout: float = 20.0) -> None:
    """After a geo fix, make the app re-read GPS rather than typing an address."""
    _open_location_picker(device)

    deadline = time.time() + timeout
    while time.time() < deadline:
        root = device.dump()
        target = next(
            (n for n in root.walk() if n.clickable and CURRENT_LOCATION_TEXT.search(f"{n.text} {n.content_desc}")),
            None,
        )
        if target is not None:
            device.tap(target.bounds)
            time.sleep(4.0)
            _tap_confirm(device)
            time.sleep(3.0)
            return
        time.sleep(1.0)

    raise DeviceError(
        "Could not find a 'use current location' control in the address picker. Run "
        "`discover` on that screen and adjust CURRENT_LOCATION_TEXT in flows.py."
    )


def _search_for_address(device: Device, query: str, timeout: float = 20.0) -> None:
    _open_location_picker(device)

    field = device.d(className="android.widget.EditText")
    if not field.wait(timeout=timeout):
        raise DeviceError("Location search field never appeared after tapping the location chip.")
    field.clear_text()
    field.set_text(query)
    time.sleep(2.5)

    result = _first_search_result(device)
    if result is None:
        raise DeviceError(f"No location suggestions appeared for {query!r}.")
    device.tap(result.bounds)
    time.sleep(3.0)

    _tap_confirm(device)
    time.sleep(4.0)


def _open_location_picker(device: Device) -> None:
    chip = _find_location_chip(device)
    if chip is None:
        raise DeviceError(
            "Could not find the location chip on the home header. Run `discover` and "
            "add the correct wording to LOCATION_CHIP_HINTS in flows.py."
        )
    device.tap(chip.bounds)
    time.sleep(2.5)


def _find_location_chip(device: Device) -> Node | None:
    root = device.dump()
    header_limit = root.bounds.height * 0.22 if root.bounds.height else 400

    best: Node | None = None
    for node in root.walk():
        if node.bounds.top > header_limit or not node.clickable:
            continue
        haystack = f"{node.resource_id} {node.content_desc} {node.text}".lower()
        if any(hint in haystack for hint in LOCATION_CHIP_HINTS):
            if best is None or node.bounds.area > best.bounds.area:
                best = node
    return best


def _first_search_result(device: Device) -> Node | None:
    root = device.dump()
    field = next((n for n in root.walk() if "EditText" in n.cls), None)
    cutoff = field.bounds.bottom if field else 0

    rows = [
        node
        for node in root.walk()
        if node.clickable and node.bounds.top > cutoff and node.bounds.height > 40 and _has_text(node)
    ]
    rows.sort(key=lambda n: n.bounds.top)
    return rows[0] if rows else None


def _tap_confirm(device: Device) -> bool:
    root = device.dump()
    for node in root.walk():
        if node.clickable and CONFIRM_TEXT.search(f"{node.text} {node.content_desc}"):
            device.tap(node.bounds)
            return True
    return False


def _has_text(node: Node) -> bool:
    return bool(node.text.strip()) or any(c.text.strip() for c in node.walk())


def _inside(rect: Rect, container: Rect, tolerance: float = 0.9) -> bool:
    if not rect.area:
        return False
    return rect.intersection_area(container) / rect.area >= tolerance


def _inside_any(rect: Rect, containers: list[Node]) -> bool:
    return any(_inside(rect, c.bounds) for c in containers)
