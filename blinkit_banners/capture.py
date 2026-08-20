from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imagehash
from PIL import Image, ImageStat

from .config import Config
from .detect import BannerCandidate
from .device import Screen
from .store import Store

# Crops flatter than this are almost always skeleton placeholders that render
# before the real creative has downloaded.
MIN_STDDEV = 6.0


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
            if _is_blank(crop):
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
    ) -> CaptureResult | None:
        if _is_blank(crop):
            return None
        return self._ingest_one(crop, location, surface, slot_index, screenshot_path)

    def _ingest_one(
        self,
        crop: Image.Image,
        location: str,
        surface: str,
        slot_index: int,
        screenshot_path: str | None,
    ) -> CaptureResult:
        digest = str(imagehash.phash(crop, hash_size=self.cfg.capture.phash_size))
        existing = self.store.match(digest, self.cfg.capture.dedupe_distance)

        if existing:
            canonical = existing
            path = self.store.creative_path(canonical) or ""
            is_new_creative = False
        else:
            canonical = digest
            path = str(self.creatives_dir / f"{canonical}.png")
            crop.save(path)
            self.store.add_creative(canonical, path, crop.width, crop.height)
            is_new_creative = True

        key = (location, surface, canonical)
        is_new_this_run = key not in self._seen_this_run
        if is_new_this_run:
            self._seen_this_run.add(key)
            self.store.add_sighting(
                phash=canonical,
                location=location,
                surface=surface,
                slot_index=slot_index,
                device_serial=self.device_serial,
                screenshot_path=screenshot_path,
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


def _is_blank(image: Image.Image) -> bool:
    stat = ImageStat.Stat(image.convert("RGB"))
    return sum(stat.stddev) / len(stat.stddev) < MIN_STDDEV
