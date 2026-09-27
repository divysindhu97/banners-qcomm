from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .capture import Collector, frames_differ
from .config import Config, Location
from .detect import find_banners, find_carousels
from .device import Device, DeviceError, crop_to
from .hierarchy import Node, Rect

# Blinkit ships UI changes often. Every selector guess lives here so there is a
# single place to correct after running `discover`.
#
# Location change, as of the screenshots:
#   1. tap the address line in the home header
#   2. type into "Search for area, street name..."
#   3. tap a suggestion card
#   4. tap "Confirm Location" on the map
#   5. dismiss whatever interstitial lands on home (layout varies by city)
LOCATION_CHIP_HINTS = ("location", "address", "deliver", "delivery")
PINCODE_RE = re.compile(r"\b\d{6}\b")
SEARCH_FIELD_HINT = re.compile(r"search for (area|a new area|street|locality)", re.IGNORECASE)
CURRENT_LOCATION_TEXT = re.compile(r"use current location|use my location|locate me|detect location", re.IGNORECASE)
CONFIRM_LOCATION_TEXT = re.compile(r"confirm location", re.IGNORECASE)
CONFIRM_TEXT = re.compile(r"confirm location|confirm|continue|proceed|select this", re.IGNORECASE)
RECENTLY_SEARCHED = re.compile(r"recently searched", re.IGNORECASE)
# Interstitials that appear on relaunch and after a location change. Anchored so
# feed copy cannot match by accident. The Ambulance-style promo is a Close "X".
DISMISS_TEXT = re.compile(
    r"^(no,?\s*thanks|not now|maybe later|not right now|skip|dismiss|later|don'?t allow|deny|close)$",
    re.IGNORECASE,
)
CLOSE_DESC = re.compile(r"^(close|dismiss|cancel)$", re.IGNORECASE)


def safe_scroll_fraction(cfg: Config) -> float:
    """Largest scroll that cannot skip a banner between two captures.

    A banner is only captured once wholly inside the visible band, so the feed
    must never advance by more than the band can hold beyond the tallest banner.
    Otherwise content passes from below the band to above it unseen.
    """
    band = 1.0 - cfg.detection.exclude_top_ratio - cfg.detection.exclude_bottom_ratio
    return max(0.15, band - cfg.feed.max_banner_height_ratio)


def find_dismissible(root: Node) -> Node | None:
    """A control that closes a modal covering the feed.

    Two shapes show up. Text buttons ('No, thanks') and icon buttons whose
    content-desc or resource-id is Close — the Ambulance promo is the latter.
    """
    for node in root.walk():
        if not node.bounds.area:
            continue
        label = (node.text or node.content_desc).strip()
        if label and DISMISS_TEXT.match(label):
            return node
        if node.clickable and CLOSE_DESC.match((node.content_desc or "").strip()):
            return node
        segment = node.resource_id.rsplit("/", 1)[-1].lower()
        if node.clickable and segment in {"close", "iv_close", "btn_close", "icon_close"}:
            return node
    return None


def dismiss_interstitial(device: Device, root: Node) -> bool:
    target = find_dismissible(root)
    if target is None:
        return False
    device.tap(target.bounds)
    time.sleep(1.5)
    return True


def dismiss_until_home(device: Device, attempts: int = 3) -> None:
    """Popups after a location change vary; keep closing them until the feed is clear."""
    for _ in range(attempts):
        root = device.dump()
        if not dismiss_interstitial(device, root):
            return


@dataclass
class SweepSummary:
    location: str
    screens: int
    sightings: int
    new_creatives: int
    carousel_visits: int = 0

    @property
    def duplicates(self) -> int:
        return max(0, self.sightings - self.new_creatives)

    def line(self) -> str:
        return (
            f"{self.location}: {self.screens} screens, {self.sightings} banner sightings, "
            f"{self.new_creatives} new, {self.duplicates} already in library, "
            f"{self.carousel_visits} carousel visits"
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

        animated = _slide_is_animated(device, crop, slide_rect, probe.scale, cfg)
        result = collector.ingest_image(crop, location, surface_name, slot_index, animated=animated)
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


def _slide_is_animated(device: Device, crop, rect: Rect, scale: float, cfg: Config) -> bool:
    """Read the same slide again to see whether it is playing video.

    Video ads draw a new frame constantly, so every screenshot of one hashes to
    a different creative and the library fills up with near-identical stills.
    A slide that redraws itself while we sit still is the one reliable tell —
    pixel similarity alone cannot separate two frames of one video from two
    genuinely different banners.

    The carousel also rotates on its own, which would look like a change, so
    the window is kept short and the change has to survive a second look.
    """
    if cfg.feed.animation_probe <= 0:
        return False
    previous = crop
    for _ in range(2):
        time.sleep(cfg.feed.animation_probe)
        try:
            again = crop_to(device.screenshot(), rect, scale)
        except ValueError:
            return False
        if not frames_differ(previous, again, cfg.capture.phash_size):
            return False
        previous = again
    return True


def set_location(device: Device, location: Location, mode: str) -> None:
    """Point the app at a delivery location using the configured strategy."""
    if mode == "manual":
        return
    _return_to_home(device)
    device.scroll_home_to_top()
    # Pin emulator GPS to the same city we are about to select. If GPS stays in
    # Mountain View, Blinkit shows 'Unserviceable area' and there is no feed.
    if location.lat is not None and location.lon is not None:
        try:
            device.geo_fix(location.lat, location.lon)
        except DeviceError:
            if mode == "geo":
                raise
    if mode == "geo":
        _use_current_location(device)
        dismiss_until_home(device)
        return
    if mode == "ui":
        query = location.address_query
        if not query:
            raise DeviceError(f"{location.name!r} needs search_query or a pincode for location_mode: ui.")
        _search_for_address(device, query)
        dismiss_until_home(device)
        return
    raise DeviceError(f"Unknown location_mode {mode!r}; expected geo, ui or manual.")


def _return_to_home(device: Device, attempts: int = 6) -> None:
    """Leave a leftover picker, map, or product-search screen from a previous city."""
    for _ in range(attempts):
        root = device.dump()
        on_map = find_confirm_location(root) is not None
        on_location_search = find_location_search_field(root) is not None
        on_home = find_location_chip(root) is not None and not on_map and not on_location_search
        if on_home:
            return
        device.back()
        time.sleep(1.2)
    device.scroll_home_to_top()


def _wait_for(device: Device, finder, timeout: float, error: str) -> Node:
    deadline = time.time() + timeout
    last: Node | None = None
    while time.time() < deadline:
        root = device.dump()
        last = finder(root)
        if last is not None:
            return last
        time.sleep(0.6)
    raise DeviceError(error)


def _use_current_location(device: Device, timeout: float = 20.0) -> None:
    """After a geo fix, tap 'Use current location' and confirm the map pin."""
    _open_location_picker(device)
    target = _wait_for(
        device,
        find_use_current_location,
        timeout,
        "Could not find 'Use current location' in the address picker. Run `discover` "
        "on that screen and check CURRENT_LOCATION_TEXT in flows.py.",
    )
    device.tap(target.bounds)
    time.sleep(2.0)
    _confirm_on_map(device)


def _search_for_address(device: Device, query: str, timeout: float = 20.0) -> None:
    """Type an area or pincode, pick a suggestion, confirm the pin on the map."""
    _open_location_picker(device)

    field = _wait_for(
        device,
        find_location_search_field,
        timeout,
        "Location search field never appeared after tapping the address line. "
        "The tap may have hit the product search bar instead.",
    )
    device.tap(field.bounds)
    time.sleep(0.4)
    typed = device.d(className="android.widget.EditText")
    if typed.exists(timeout=3):
        typed.set_text(query)
    else:
        raise DeviceError("Could not focus the location search field to type an address.")
    time.sleep(1.5)

    suggestion = _wait_for(
        device,
        find_search_suggestion,
        timeout,
        f"No location suggestions appeared for {query!r}.",
    )
    device.tap(suggestion.bounds)
    time.sleep(2.0)
    _confirm_on_map(device, timeout=35.0)


def _confirm_on_map(device: Device, timeout: float = 20.0) -> None:
    """The map screen is its own step: wait for 'Confirm Location' and tap it."""
    button = _wait_for(
        device,
        find_confirm_location,
        timeout,
        "The map 'Confirm Location' button never appeared after picking an address.",
    )
    device.tap(button.bounds)
    time.sleep(3.0)


def _open_location_picker(device: Device, attempts: int = 3, timeout: float = 9.0) -> None:
    """Tap the address line and wait for the picker sheet to slide up.

    The sheet takes a couple of seconds on a warm app and much longer on a cold
    one, and a tap that lands while the feed is still settling is swallowed
    entirely. So poll for the field, and tap again rather than give up on the
    first miss.
    """
    for _ in range(attempts):
        chip = find_location_chip(device.dump())
        if chip is None:
            raise DeviceError(
                "Could not find the address line on the home header. Run `discover` and "
                "check the top of annotated.png."
            )
        device.tap(chip.bounds)

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.6)
            if find_location_search_field(device.dump()) is not None:
                return

        # A swallowed tap leaves us on home; one that opened something else has
        # to be backed out of before the address line is reachable again.
        if find_location_chip(device.dump()) is None:
            device.back()
            time.sleep(1.2)

    raise DeviceError(
        "Tapped the address line but the location picker did not open. "
        "Run `discover` and check the header on annotated.png."
    )


def find_location_chip(root: Node) -> Node | None:
    """The tappable address under the delivery-time header.

    On the current Blinkit build the visible address (`subtitle2`) is not itself
    clickable — the parent `container` is. Prefer the smallest clickable ancestor
    so we do not hit the full-screen root or the profile icon.
    """
    header_limit = root.bounds.height * 0.22 if root.bounds.height else 400
    scored: list[tuple[int, Node]] = []
    for node in root.walk():
        if node.bounds.top > header_limit:
            continue
        text = (node.text or "").strip()
        haystack = f"{node.resource_id} {node.content_desc} {text}".lower()
        segment = node.resource_id.rsplit("/", 1)[-1]
        if "systemui" in node.resource_id or "location in use" in haystack:
            continue
        if text.lower().startswith("pin location"):
            continue
        score = 0
        if segment == "subtitle2":
            score += 4
        if PINCODE_RE.search(text):
            score += 3
        if any(hint in haystack for hint in LOCATION_CHIP_HINTS):
            score += 2
        if text.count(",") >= 1 and len(text) >= 12:
            score += 1
        if not score:
            continue
        # Tap the label's own box. The parent container is clickable but also
        # contains the 'km away' chip and wallet; clicking its centre misses the picker.
        scored.append((score, node))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].bounds.area))
    return scored[0][1]


def _smallest_clickable(node: Node, header_limit: float | None = None) -> Node | None:
    candidates = []
    chain = [node, *node.ancestors()]
    for item in chain:
        if not item.clickable:
            continue
        if header_limit is not None and item.bounds.top > header_limit:
            continue
        candidates.append(item)
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.bounds.area)


def find_location_search_field(root: Node) -> Node | None:
    """The picker/map address field, never the homepage 'Search for atta, dal' bar."""
    for node in root.walk():
        if "EditText" not in node.cls:
            continue
        blob = f"{node.hint} {node.text} {node.content_desc}"
        if SEARCH_FIELD_HINT.search(blob):
            return node
    return None


def find_use_current_location(root: Node) -> Node | None:
    for node in root.walk():
        if CURRENT_LOCATION_TEXT.search(f"{node.text} {node.content_desc}"):
            return _smallest_clickable(node)
        if node.resource_id.rsplit("/", 1)[-1] == "use_my_location" and node.clickable:
            return node
    return None


def find_search_suggestion(root: Node) -> Node | None:
    """First suggestion card under the search field, never 'Use current location'."""
    field = next((node for node in root.walk() if "EditText" in node.cls), None)
    cutoff = field.bounds.bottom if field else 0
    rows = []
    for node in root.walk():
        if not node.clickable or node.bounds.top <= cutoff or node.bounds.height < 60:
            continue
        blob = f"{node.text} {node.content_desc}"
        if CURRENT_LOCATION_TEXT.search(blob) or RECENTLY_SEARCHED.search(blob):
            continue
        if CLOSE_DESC.match((node.content_desc or "").strip()):
            continue
        if not _has_text(node):
            continue
        rows.append(node)
    rows.sort(key=lambda node: node.bounds.top)
    return rows[0] if rows else None


def find_confirm_location(root: Node) -> Node | None:
    """The green button at the bottom of the map. Label often lives on a child view."""
    matches: list[Node] = []
    for node in root.walk():
        segment = node.resource_id.rsplit("/", 1)[-1]
        if segment == "tv_toolbar_title":
            continue
        blob = f"{node.text} {node.content_desc}"
        if segment in {"enter_address", "btn_confirm", "confirm_location"} or CONFIRM_LOCATION_TEXT.search(blob):
            target = _smallest_clickable(node)
            if target is not None:
                matches.append(target)
    if not matches:
        return None
    return max(matches, key=lambda node: (node.bounds.top, node.bounds.area))


def _has_text(node: Node) -> bool:
    return bool(node.text.strip()) or any(c.text.strip() for c in node.walk())


def _inside(rect: Rect, container: Rect, tolerance: float = 0.9) -> bool:
    if not rect.area:
        return False
    return rect.intersection_area(container) / rect.area >= tolerance


def _inside_any(rect: Rect, containers: list[Node]) -> bool:
    return any(_inside(rect, c.bounds) for c in containers)
