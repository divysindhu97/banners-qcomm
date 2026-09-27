from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imagehash
from PIL import Image, ImageStat

from .config import Config, DetectionConfig, Location
from .detect import BannerCandidate
from .device import Screen
from .store import Store

# Crops flatter than this are almost always skeleton placeholders that render
# before the real creative has downloaded.
MIN_STDDEV = 6.0
# Splash / tagline frames ("AN ETERNAL COMPANY", a lone "doorstep") are one
# colour plus a line of type, so stddev can still clear MIN_STDDEV. Reject
# anything whose dominant colour bucket covers this much of the crop.
MAX_DOMINANT_SHARE = 0.90
_COLOR_BUCKET = 16
_PLACEHOLDER_SAMPLE = (160, 112)
# Occasion headers sit at ~0.19–0.31. Hero ~0.70, promo tiles ~0.71, store
# promos ~0.50. Half of a product rail is ~600px wide at ~0.58.
HEADER_ASPECT = 0.45
NARROW_CROP_PX = 900
MIN_TILE_ASPECT = 0.65
_CHROME_PHRASES = (
    "frequently bought",
    "quently bought",
    "quentlybought",
    "explore all rakhi",
)
_PLUS_MORE = re.compile(r"\+\s*\d+\s*more", re.IGNORECASE)
# Two reads of a static banner differ only by screenshot noise. Anything past
# this means the slide redrew itself, which only video does.
ANIMATION_MIN_DISTANCE = 20


def frames_differ(first: Image.Image, second: Image.Image, hash_size: int) -> bool:
    """True when two reads of the same slide show different pixels."""
    before = imagehash.phash(first, hash_size=hash_size)
    after = imagehash.phash(second, hash_size=hash_size)
    return (before - after) > ANIMATION_MIN_DISTANCE


@dataclass
class CaptureResult:
    phash: str
    is_new_creative: bool
    is_new_this_run: bool
    path: str
    slot_index: int


class Collector:
    def __init__(self, cfg: Config, store: Store, device_serial: str | None = None):
        self.cfg = cfg
        self.store = store
        self.device_serial = device_serial
        self.creatives_dir = cfg.output_path / "creatives"
        self.screens_dir = cfg.output_path / "screens"
        self.creatives_dir.mkdir(parents=True, exist_ok=True)
        if cfg.capture.save_full_screenshots:
            self.screens_dir.mkdir(parents=True, exist_ok=True)
        self._seen_this_run: set[tuple[str, str, str]] = set()
        # Set before each city sweep so every sighting carries city and pincode.
        self.place: Location | None = None

    def save_screenshot(self, screen: Screen, location: str, surface: str) -> str | None:
        if not self.cfg.capture.save_full_screenshots:
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = self.screens_dir / f"{location}_{surface}_{stamp}.png"
        screen.image.save(path)
        return str(path)

    def ingest(
        self,
        screen: Screen,
        candidates: list[BannerCandidate],
        location: str,
        surface: str,
        screenshot_path: str | None = None,
    ) -> list[CaptureResult]:
        results: list[CaptureResult] = []
        for index, candidate in enumerate(candidates):
            try:
                crop = screen.crop(candidate.rect)
            except ValueError:
                continue
            if _should_skip(crop, self.cfg.detection):
                continue
            results.append(self._ingest_one(crop, location, surface, index, screenshot_path))
        return results

    def ingest_image(
        self,
        crop: Image.Image,
        location: str,
        surface: str,
        slot_index: int,
        screenshot_path: str | None = None,
        animated: bool = False,
    ) -> CaptureResult | None:
        if _should_skip(crop, self.cfg.detection):
            return None
        return self._ingest_one(crop, location, surface, slot_index, screenshot_path, animated)

    def _ingest_one(
        self,
        crop: Image.Image,
        location: str,
        surface: str,
        slot_index: int,
        screenshot_path: str | None,
        animated: bool = False,
    ) -> CaptureResult:
        digest = str(imagehash.phash(crop, hash_size=self.cfg.capture.phash_size))
        existing = self.store.match(
            digest,
            self.cfg.capture.dedupe_distance,
            animated=animated,
            animated_distance=self.cfg.capture.video_dedupe_distance,
        )

        if existing:
            canonical = existing
            path = self.store.creative_path(canonical) or ""
            is_new_creative = False
        else:
            canonical = digest
            path = str(self.creatives_dir / f"{canonical}.png")
            crop.save(path)
            self.store.add_creative(canonical, path, crop.width, crop.height, is_video=animated)
            is_new_creative = True

        key = (location, surface, canonical)
        is_new_this_run = key not in self._seen_this_run
        if is_new_this_run:
            self._seen_this_run.add(key)
            city = self.place.city if self.place else ""
            pincode = self.place.pincode if self.place else ""
            self.store.add_sighting(
                phash=canonical,
                location=location,
                surface=surface,
                slot_index=slot_index,
                device_serial=self.device_serial,
                screenshot_path=screenshot_path,
                city=city,
                pincode=pincode,
            )

        return CaptureResult(
            phash=canonical,
            is_new_creative=is_new_creative,
            is_new_this_run=is_new_this_run,
            path=path,
            slot_index=slot_index,
        )

    def reset_run(self) -> None:
        self._seen_this_run.clear()


def is_banner_shape(width: int, height: int, detection: DetectionConfig | None = None) -> bool:
    """True for hero ads, promo tiles, and wide store promos — not headers or rails."""
    if width <= 0 or height <= 0:
        return False
    aspect = height / width
    min_aspect = max(HEADER_ASPECT, detection.min_aspect if detection else HEADER_ASPECT)
    max_aspect = detection.max_aspect if detection else 1.0
    if not (min_aspect <= aspect <= max_aspect):
        return False
    if width < NARROW_CROP_PX and aspect < MIN_TILE_ASPECT:
        return False
    return True


def is_feed_chrome(headline: str = "", ocr_text: str = "") -> bool:
    """Product rails and 'frequently bought' mosaics that land in a banner slot."""
    blob = f"{headline} {ocr_text}".lower()
    if any(phrase in blob for phrase in _CHROME_PHRASES):
        return True
    return len(_PLUS_MORE.findall(blob)) >= 2


def purge_non_banners(store: Store, detection: DetectionConfig | None = None) -> int:
    """Drop stored crops that are headers, product rails, or feed chrome."""
    removed = 0
    for row in list(store.creatives()):
        attrs = store.conn.execute(
            "SELECT headline, ocr_text FROM attributes WHERE phash = ?",
            (row["phash"],),
        ).fetchone()
        headline = attrs["headline"] if attrs else ""
        ocr_text = attrs["ocr_text"] if attrs else ""
        if is_banner_shape(row["width"], row["height"], detection) and not is_feed_chrome(headline, ocr_text):
            continue
        path = store.delete_creative(row["phash"])
        if path and path.exists():
            path.unlink()
        removed += 1
    return removed


def _should_skip(image: Image.Image, detection: DetectionConfig | None = None) -> bool:
    return _is_blank(image) or _is_placeholder(image) or not is_banner_shape(image.width, image.height, detection)


def _is_blank(image: Image.Image) -> bool:
    stat = ImageStat.Stat(image.convert("RGB"))
    return sum(stat.stddev) / len(stat.stddev) < MIN_STDDEV


def _is_placeholder(image: Image.Image) -> bool:
    """True when almost every pixel is the same colour — splash, not an ad."""
    sample = image.convert("RGB").resize(_PLACEHOLDER_SAMPLE, Image.Resampling.BILINEAR)
    counts: dict[tuple[int, int, int], int] = {}
    raw = sample.tobytes()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        key = (r - r % _COLOR_BUCKET, g - g % _COLOR_BUCKET, b - b % _COLOR_BUCKET)
        counts[key] = counts.get(key, 0) + 1
    n = sample.width * sample.height
    return max(counts.values()) / n >= MAX_DOMINANT_SHARE
