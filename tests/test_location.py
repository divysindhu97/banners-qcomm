"""Location picker matching the real Blinkit screenshots: address line, search, map confirm, close X."""

from __future__ import annotations

import pytest

from blinkit_banners import flows
from blinkit_banners.device import DeviceError
from blinkit_banners.flows import (
    _open_location_picker,
    find_confirm_location,
    find_dismissible,
    find_location_chip,
    find_location_search_field,
    find_search_suggestion,
    find_use_current_location,
)
from blinkit_banners.hierarchy import parse

HOME = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" text="Blinkit in" bounds="[24,40][200,80]"/>
    <node class="android.widget.TextView" text="8 minutes" bounds="[24,80][280,140]"/>
    <node class="android.widget.TextView" clickable="true"
          text="Kokapet, Hyderabad, Telangana, 500075, India"
          bounds="[24,145][720,200]"/>
    <node class="android.widget.EditText" text="Search for atta, dal, coke and more"
          bounds="[40,220][1040,320]"/>
    <node class="android.widget.TextView" text="Bestsellers" bounds="[40,1800][400,1860]"/>
  </node>
</hierarchy>
"""

PICKER = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.ImageView" content-desc="Close" clickable="true"
          bounds="[500,80][580,160]"/>
    <node class="android.widget.TextView" text="Select delivery location"
          bounds="[40,180][700,240]"/>
    <node class="android.widget.EditText" text="Search for area, street name..."
          hint="Search for area, street name..."
          bounds="[40,260][1040,360]"/>
    <node class="android.widget.TextView" text="Use current location" clickable="true"
          bounds="[40,380][1040,470]"/>
    <node class="android.widget.TextView" text="Recently searched locations"
          bounds="[40,500][700,540]"/>
    <node class="android.widget.LinearLayout" clickable="true" bounds="[40,560][1040,700]">
      <node class="android.widget.TextView" text="Kokapet" bounds="[120,570][500,620]"/>
      <node class="android.widget.TextView" text="Hyderabad, Telangana, 500075, India"
            bounds="[120,630][900,680]"/>
    </node>
  </node>
</hierarchy>
"""

SUGGESTIONS = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.ImageView" content-desc="Close" clickable="true"
          bounds="[500,80][580,160]"/>
    <node class="android.widget.TextView" text="Select delivery location"
          bounds="[40,180][700,240]"/>
    <node class="android.widget.EditText" text="110091" bounds="[40,260][1040,360]"/>
    <node class="android.widget.LinearLayout" clickable="true" bounds="[40,400][1040,560]">
      <node class="android.widget.TextView" text="110091" bounds="[140,420][400,470]"/>
      <node class="android.widget.TextView"
            text="Chilla Sarda Bangar, Mayur Vihar, New Delhi, Delhi, India"
            bounds="[140,480][980,530]"/>
    </node>
    <node class="android.widget.LinearLayout" clickable="true" bounds="[40,580][1040,740]">
      <node class="android.widget.TextView" text="110091" bounds="[140,600][400,650]"/>
      <node class="android.widget.TextView"
            text="Chilla Gaon, Mayur Vihar Phase 1 Extension, New Delhi"
            bounds="[140,660][980,710]"/>
    </node>
  </node>
</hierarchy>
"""

MAP = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" text="Confirm location" bounds="[80,40][600,100]"/>
    <node class="android.widget.EditText" text="Search for a new area, locality..."
          bounds="[40,120][1040,210]"/>
    <node class="android.widget.TextView" text="Use current location" clickable="true"
          bounds="[300,1500][780,1580]"/>
    <node class="android.widget.TextView" text="Delivering your order to"
          bounds="[40,1700][600,1750]"/>
    <node class="android.widget.TextView" text="Delhi Division" bounds="[40,1760][400,1810]"/>
    <node class="android.widget.TextView" text="Change" clickable="true"
          bounds="[820,1760][1020,1820]"/>
    <node class="android.widget.Button" text="Confirm Location" clickable="true"
          bounds="[40,2200][1040,2340]"/>
  </node>
</hierarchy>
"""

AMBULANCE = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" text="Kokapet, Hyderabad, Telangana, 500075"
          clickable="true" bounds="[24,145][720,200]"/>
    <node class="android.widget.FrameLayout" bounds="[40,700][1040,2100]">
      <node class="android.widget.ImageView" content-desc="Close" clickable="true"
            bounds="[920,720][1020,820]"/>
      <node class="android.widget.TextView" text="Blinkit Ambulance"
            bounds="[80,1600][1000,1700]"/>
      <node class="android.widget.TextView" text="Now live in your area"
            bounds="[80,1900][1000,1980]"/>
    </node>
  </node>
</hierarchy>
"""


def test_product_search_is_not_the_location_field():
    home = parse(HOME)
    assert find_location_search_field(home) is None
    picker = parse(PICKER)
    field = find_location_search_field(picker)
    assert field is not None
    assert "area" in (field.hint or field.text).lower()
    chip = find_location_chip(parse(HOME))
    assert chip is not None
    assert "500075" in chip.text
    assert "Kokapet" in chip.text


def test_product_search_bar_is_not_the_address_line():
    chip = find_location_chip(parse(HOME))
    assert chip is not None
    assert "atta" not in chip.text.lower()


def test_picker_skips_use_current_location_when_picking_a_suggestion():
    root = parse(PICKER)
    current = find_use_current_location(root)
    assert current is not None and "Use current location" in current.text
    suggestion = find_search_suggestion(root)
    assert suggestion is not None
    assert "Kokapet" in " ".join(n.text for n in suggestion.walk())
    assert suggestion is not current


def test_typed_pincode_picks_the_first_suggestion_card():
    suggestion = find_search_suggestion(parse(SUGGESTIONS))
    assert suggestion is not None
    blob = " ".join(n.text for n in suggestion.walk())
    assert "110091" in blob
    assert "Chilla Sarda Bangar" in blob


def test_map_confirm_is_the_bottom_button_not_change():
    button = find_confirm_location(parse(MAP))
    assert button is not None
    assert button.text == "Confirm Location"
    assert button.bounds.top > 2000


REAL_MAP_BUTTON = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1280,2856]">
    <node class="android.widget.TextView" resource-id="com.grofers.customerapp:id/tv_toolbar_title"
          text="Confirm location" bounds="[168,211][1280,270]"/>
    <node class="android.view.ViewGroup" resource-id="com.grofers.customerapp:id/enter_address"
          clickable="true" bounds="[36,2604][1244,2748]">
      <node class="android.view.View" content-desc="Confirm Location" bounds="[401,2642][879,2709]"/>
    </node>
  </node>
</hierarchy>
"""


def test_confirm_taps_enter_address_when_label_is_on_a_child():
    button = find_confirm_location(parse(REAL_MAP_BUTTON))
    assert button is not None
    assert button.resource_id.endswith("enter_address")


def test_ambulance_popup_closes_on_the_x():
    target = find_dismissible(parse(AMBULANCE))
    assert target is not None
    assert target.content_desc == "Close"


UNSERVICEABLE = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1280,2856]">
    <node class="android.view.ViewGroup" clickable="true" bounds="[0,0][1280,2856]"
          resource-id="com.grofers.customerapp:id/container">
      <node class="android.view.ViewGroup" clickable="true"
            resource-id="com.grofers.customerapp:id/container" bounds="[36,93][1088,304]">
        <node class="android.view.View" resource-id="com.grofers.customerapp:id/subtitle"
              content-desc="Unserviceable area" bounds="[36,120][725,215]"/>
        <node class="android.widget.TextView" resource-id="com.grofers.customerapp:id/subtitle2"
              text="Santa Clara County, Mountain View" bounds="[36,227][793,286]"/>
      </node>
      <node class="android.widget.ImageView" content-desc="Go to profile" clickable="true"
            resource-id="com.grofers.customerapp:id/right_image" bounds="[1136,157][1244,265]"/>
    </node>
  </node>
</hierarchy>
"""


def test_address_chip_taps_the_label_not_the_whole_header():
    chip = find_location_chip(parse(UNSERVICEABLE))
    assert chip is not None
    assert chip.resource_id.endswith("subtitle2")
    assert "Mountain View" in chip.text


class FakeDevice:
    """Home screen that only reveals the picker after `opens_after` dumps."""

    def __init__(self, opens_after: int | None):
        self.opens_after = opens_after
        self.dumps = 0
        self.taps = 0
        self.backs = 0

    def dump(self):
        self.dumps += 1
        opened = self.opens_after is not None and self.dumps > self.opens_after
        return parse(PICKER if opened else HOME)

    def tap(self, bounds):
        self.taps += 1

    def back(self):
        self.backs += 1


@pytest.fixture
def fast_clock(monkeypatch):
    """Sleeping advances a virtual clock, so waits are exercised without the wait."""
    now = [1000.0]

    def sleep(seconds):
        now[0] += seconds

    monkeypatch.setattr(flows.time, "sleep", sleep)
    monkeypatch.setattr(flows.time, "time", lambda: now[0])


def test_picker_opening_slowly_is_waited_out(fast_clock):
    """A cold app takes several seconds to slide the sheet up; that is not a failure."""
    device = FakeDevice(opens_after=6)
    _open_location_picker(device)
    assert device.taps == 1


def test_a_swallowed_tap_on_the_address_line_is_retried(fast_clock):
    """Taps during feed settle do nothing at all, so one miss must not end the sweep."""
    device = FakeDevice(opens_after=20)
    _open_location_picker(device)
    assert device.taps > 1


def test_picker_that_never_opens_still_raises(fast_clock):
    device = FakeDevice(opens_after=None)
    with pytest.raises(DeviceError, match="did not open"):
        _open_location_picker(device)
    assert device.taps == 3
