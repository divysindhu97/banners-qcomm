from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .humanize import HumanConfig


@dataclass
class DeviceConfig:
    serial: str | None = None
    package: str = "com.grofers.customerapp"
    launch_settle: float = 6.0


@dataclass
class CaptureConfig:
    output_dir: str = "data"
    save_full_screenshots: bool = True
    phash_size: int = 16
    dedupe_distance: int = 32


@dataclass
class DetectionConfig:
    min_width_ratio: float = 0.80
    min_aspect: float = 0.22
    max_aspect: float = 1.00
    min_height_px: int = 120
    # Fractions of screen height hidden behind sticky overlays.
    exclude_top_ratio: float = 0.04
    exclude_bottom_ratio: float = 0.10
    # Require the whole banner inside the visible band. A partly-scrolled or
    # overlay-covered view crops badly and hashes differently from the same
    # creative seen in full, which would split one banner across several entries.
    require_fully_visible: bool = True
    # Exact matches on the id segment, e.g. `image_view`, not the full path.
    resource_id_allow: list[str] = field(default_factory=list)
    resource_id_deny: list[str] = field(default_factory=list)
    # Substring, case-insensitive, matched against content-desc.
    content_desc_allow: list[str] = field(default_factory=list)
    # Ignore these containers and everything inside them.
    container_deny: list[str] = field(default_factory=list)
    # False: known ids plus anything else banner-shaped, so an app update that
    # renames ids degrades to geometry instead of capturing nothing.
    # True: only the ids in resource_id_allow.
    strict_ids: bool = False


@dataclass
class FeedConfig:
    scroll_steps: int = 20
    carousel_max_slides: int = 18
    # Consecutive slides already recorded this run before a carousel is done.
    carousel_stall_limit: int = 3
    # Tallest banner we expect, as a fraction of screen height. Scrolling is
    # capped so a banner this tall cannot fall between two captures.
    max_banner_height_ratio: float = 0.30


@dataclass
class Location:
    name: str
    search_query: str = ""
    lat: float | None = None
    lon: float | None = None


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    human: HumanConfig = field(default_factory=HumanConfig)
    # geo: set emulator GPS, then tap "use current location" (most reliable)
    # ui: type the address into the in-app picker (fragile, works on real phones)
    # manual: never change location; capture whatever the device is set to
    location_mode: str = "geo"
    locations: list[Location] = field(default_factory=list)

    @property
    def output_path(self) -> Path:
        return Path(self.capture.output_dir)


def _build(cls, raw: dict | None):
    """Instantiate a config dataclass, coercing YAML lists into tuple ranges."""
    raw = raw or {}
    tuple_fields = {f.name for f in fields(cls) if "Range" in str(f.type) or f.type == "Range"}
    coerced = {
        key: tuple(value) if key in tuple_fields and isinstance(value, list) else value
        for key, value in raw.items()
    }
    return cls(**coerced)


def load(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Config(
        device=_build(DeviceConfig, raw.get("device")),
        capture=_build(CaptureConfig, raw.get("capture")),
        detection=_build(DetectionConfig, raw.get("detection")),
        feed=_build(FeedConfig, raw.get("feed")),
        human=_build(HumanConfig, raw.get("human")),
        location_mode=raw.get("location_mode", "geo"),
        locations=[Location(**loc) for loc in (raw.get("locations") or [])],
    )
