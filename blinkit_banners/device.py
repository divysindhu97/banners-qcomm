from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import uiautomator2 as u2
from PIL import Image

from .config import DeviceConfig
from .hierarchy import Node, Rect, parse
from .humanize import Human, HumanConfig


class DeviceError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def adb_binary() -> str:
    """Locate adb without relying on it being on PATH.

    An Android Studio install leaves adb under the SDK directory and does not add
    it to PATH, so fall back to the SDK location and then to the copy adbutils
    ships with.
    """
    found = shutil.which("adb")
    if found:
        return found

    for root in filter(None, (os.getenv("ANDROID_HOME"), os.getenv("ANDROID_SDK_ROOT"),
                              os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk"))):
        candidate = Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        if candidate.exists():
            return str(candidate)

    try:
        from adbutils import adb_path

        return adb_path()
    except Exception:  # noqa: BLE001 - last resort, let the caller report the failure
        return "adb"


def crop_to(image: Image.Image, rect: Rect, scale: float) -> Image.Image:
    box = rect.scaled(scale)
    left = max(0, box.left)
    top = max(0, box.top)
    right = min(image.width, box.right)
    bottom = min(image.height, box.bottom)
    if right <= left or bottom <= top:
        raise ValueError(f"empty crop for {rect}")
    return image.crop((left, top, right, bottom))


@dataclass
class Screen:
    """A screenshot paired with the view hierarchy captured alongside it."""

    image: Image.Image
    root: Node
    scale: float

    def crop(self, rect: Rect) -> Image.Image:
        return crop_to(self.image, rect, self.scale)


class Device:
    def __init__(self, cfg: DeviceConfig, human: Human | None = None):
        self.cfg = cfg
        self.human = human or Human(HumanConfig(enabled=False))
        self.counts: Counter[str] = Counter()
        self._window_size: tuple[int, int] | None = None
        try:
            self.d = u2.connect(cfg.serial) if cfg.serial else u2.connect()
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
            raise DeviceError(
                "Could not reach a device over ADB. Check `adb devices` shows one "
                "device in state `device`, and that USB debugging is authorised."
            ) from exc

    @property
    def serial(self) -> str:
        return self.d.serial

    @property
    def is_emulator(self) -> bool:
        if self.serial.startswith("emulator-"):
            return True
        return bool(self.d.shell("getprop ro.boot.qemu").output.strip() == "1")

    def window_size(self) -> tuple[int, int]:
        if self._window_size is None:
            self._window_size = self.d.window_size()
        return self._window_size

    def screenshot(self) -> Image.Image:
        """Pixels only. Much cheaper than `capture`, which also dumps the hierarchy."""
        self.counts["screenshot"] += 1
        return self.d.screenshot().convert("RGB")

    def scale(self, image: Image.Image) -> float:
        width, _ = self.window_size()
        return image.width / width if width else 1.0

    def launch_app(self) -> None:
        self.d.app_start(self.cfg.package, stop=True, wait=True)
        time.sleep(self.cfg.launch_settle)

    def ensure_foreground(self) -> None:
        current = self.d.app_current().get("package")
        if current != self.cfg.package:
            raise DeviceError(
                f"Expected {self.cfg.package} in the foreground but found {current}. "
                "The app may have crashed, logged out, or shown an interstitial."
            )

    def dump(self) -> Node:
        return parse(self.d.dump_hierarchy(compressed=False))

    def capture(self) -> Screen:
        """Screenshot and hierarchy, taken as close together as possible."""
        self.counts["capture"] += 1
        image = self.d.screenshot()
        xml = self.d.dump_hierarchy(compressed=False)
        root = parse(xml)
        width, _ = self.window_size()
        scale = image.width / width if width else 1.0
        return Screen(image=image.convert("RGB"), root=root, scale=scale)

    def tap(self, rect: Rect) -> None:
        self.counts["tap"] += 1
        x = self.human.jitter(rect.left + rect.width // 2, min(self.human.cfg.tap_jitter_px, rect.width // 3))
        y = self.human.jitter(rect.top + rect.height // 2, min(self.human.cfg.tap_jitter_px, rect.height // 3))
        self.d.click(x, y)

    def scroll_feed(self, cap: float | None = None) -> None:
        self.counts["scroll"] += 1
        width, height = self.window_size()
        distance = self.human.scroll_distance()
        if cap is not None:
            distance = min(distance, cap)
        x = self.human.jitter(width // 2, width // 8)
        start_y = int(height * 0.80)
        end_y = max(int(height * 0.05), int(height * (0.80 - distance)))
        self.d.swipe(x, start_y, x, end_y, self.human.swipe_duration())

    def swipe_carousel(self, rect: Rect) -> None:
        self.counts["carousel_swipe"] += 1
        y = self.human.jitter(rect.top + rect.height // 2, max(1, rect.height // 6))
        start_x = rect.left + int(rect.width * 0.85)
        end_x = rect.left + int(rect.width * 0.15)
        self.d.swipe(start_x, y, end_x, y, self.human.swipe_duration())

    def geo_fix(self, lat: float, lon: float) -> None:
        """Set GPS coordinates through the emulator console. Emulator only."""
        command = [adb_binary(), "-s", self.serial, "emu", "geo", "fix", str(lon), str(lat)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError(f"`adb emu geo fix` failed to run: {exc}") from exc

        output = f"{result.stdout}{result.stderr}".strip()
        if result.returncode != 0 or "KO" in output:
            raise DeviceError(
                f"`adb emu geo fix` was rejected ({output or 'no output'}). This command "
                "only works on emulators; use location_mode: ui on a physical phone."
            )

    def back(self) -> None:
        self.d.press("back")
