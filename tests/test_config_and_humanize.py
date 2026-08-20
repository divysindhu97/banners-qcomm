from __future__ import annotations

import textwrap

from blinkit_banners import config as config_module
from blinkit_banners.humanize import Human, HumanConfig

YAML = textwrap.dedent(
    """
    device:
      package: com.grofers.customerapp
    detection:
      strict_ids: true
      resource_id_allow: [banner_image]
    human:
      enabled: true
      scroll_distance: [0.4, 0.9]
      location_gap: [5.0, 10.0]
    location_mode: ui
    locations:
      - name: gurgaon
        lat: 28.4949
        lon: 77.0895
        search_query: DLF Phase 3, Gurugram
      - name: bengaluru
        search_query: Indiranagar, Bengaluru
    """
)


def test_yaml_lists_become_ranges(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    cfg = config_module.load(path)

    assert cfg.human.scroll_distance == (0.4, 0.9)
    assert cfg.human.location_gap == (5.0, 10.0)
    # Untouched fields keep their dataclass defaults.
    assert cfg.human.settle == HumanConfig().settle


def test_loads_locations_and_mode(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(YAML, encoding="utf-8")
    cfg = config_module.load(path)

    assert cfg.location_mode == "ui"
    assert cfg.detection.strict_ids is True
    assert [loc.name for loc in cfg.locations] == ["gurgaon", "bengaluru"]
    assert cfg.locations[0].lat == 28.4949
    assert cfg.locations[1].lat is None


def test_disabled_human_is_deterministic():
    human = Human(HumanConfig(enabled=False, scroll_distance=(0.5, 0.9)))
    assert human.scroll_distance() == 0.7
    assert human.jitter(500) == 500
    assert human.order([1, 2, 3]) == [1, 2, 3]


def test_enabled_human_varies_within_bounds():
    human = Human(HumanConfig(enabled=True, scroll_distance=(0.5, 0.9)), seed=7)
    samples = [human.scroll_distance() for _ in range(50)]
    assert all(0.5 <= s <= 0.9 for s in samples)
    assert len(set(samples)) > 40


def test_jitter_stays_within_its_limit():
    human = Human(HumanConfig(enabled=True, tap_jitter_px=12), seed=3)
    assert all(abs(human.jitter(500) - 500) <= 12 for _ in range(50))
    assert all(abs(human.jitter(500, limit=3) - 500) <= 3 for _ in range(50))


def test_location_order_is_shuffled_but_complete():
    names = [f"city-{i}" for i in range(10)]
    human = Human(HumanConfig(enabled=True), seed=1)
    shuffled = human.order(names)
    assert sorted(shuffled) == sorted(names)
    assert shuffled != names


def test_shuffle_can_be_turned_off():
    names = [f"city-{i}" for i in range(10)]
    human = Human(HumanConfig(enabled=True, shuffle_locations=False), seed=1)
    assert human.order(names) == names
