from __future__ import annotations

from blinkit_banners.dashboard import _when, render
from blinkit_banners.flows import SweepSummary
from blinkit_banners.store import ATTRIBUTE_FIELDS, Store


def test_summary_separates_new_from_duplicates():
    summary = SweepSummary("Delhi 110001", screens=10, sightings=8, new_creatives=3, carousel_visits=1)
    assert summary.duplicates == 5
    assert "3 new, 5 already in library" in summary.line()


def test_scrape_date_is_shown_in_ist():
    stamp = "2026-08-20T19:22:34+00:00"
    assert _when(stamp, stamp) == "Seen 21 Aug 2026"
    assert _when("2026-08-21T04:00:00+00:00", "2026-08-24T10:00:00+00:00") == "Seen 21–24 Aug 2026"


def _seed(store, phash, headline, city="Delhi", pincode="110001", path="creative.png",
          first_seen=None, category="", occasion=""):
    store.add_creative(phash, path, 1024, 716)
    store.add_sighting(phash, f"{city.lower()}-loc", "home", 0, city=city, pincode=pincode)
    if first_seen:
        store.conn.execute("UPDATE creatives SET first_seen = ? WHERE phash = ?", (first_seen, phash))
        store.conn.execute("UPDATE sightings SET captured_at = ? WHERE phash = ?", (first_seen, phash))
        store.conn.commit()
    values = {field: None for field in ATTRIBUTE_FIELDS}
    values.update(headline=headline, has_ad_badge=0, category=category, occasion=occasion)
    store.save_attributes(phash, values)


def test_dashboard_lists_scrape_date_city_and_uses_white_background(tmp_path):
    store = Store(tmp_path / "banners.db")
    image = tmp_path / "creative.png"
    image.write_bytes(b"")
    phash = "ab" * 32
    _seed(store, phash, "The perfect Rakhi gifts start here", path=str(image))

    destination = tmp_path / "dashboard.html"
    render(store.library(), destination, store.place_summaries())
    body = destination.read_text(encoding="utf-8")

    assert "Delhi 110001" in body
    assert "Seen" in body
    assert "CTA:" not in body
    assert "swatches" not in body
    assert 'data-city="Delhi"' in body
    assert "--bg: #f4f5f7" in body
    store.close()


def test_featured_slider_is_gone(tmp_path):
    store = Store(tmp_path / "banners.db")
    image = tmp_path / "creative.png"
    image.write_bytes(b"")
    _seed(store, "aa" * 32, "A banner", path=str(image))

    destination = tmp_path / "dashboard.html"
    render(store.library(), destination, store.place_summaries())
    body = destination.read_text(encoding="utf-8")

    assert 'class="track"' not in body
    assert "Featured now" not in body
    assert "setInterval" not in body
    store.close()


def test_toolbar_has_search_pills_and_dropdowns(tmp_path):
    store = Store(tmp_path / "banners.db")
    image = tmp_path / "creative.png"
    image.write_bytes(b"")
    _seed(store, "aa" * 32, "Home banner", path=str(image), category="Home", occasion="Raksha Bandhan")
    _seed(store, "bb" * 32, "Chocolate banner", path=str(image), category="Chocolate")

    destination = tmp_path / "dashboard.html"
    render(store.library(), destination, store.place_summaries())
    body = destination.read_text(encoding="utf-8")

    assert 'class="toolbar"' in body
    assert 'placeholder="Search headline, copy, brand, category' in body
    assert 'data-quick="">All</button>' in body
    assert 'data-quick="occasions">Occasions</button>' in body
    assert 'data-quick="Home">Home</button>' in body
    assert 'data-quick="Chocolate">Chocolate</button>' in body
    assert 'data-key="brand"' in body
    assert 'data-key="city"' in body
    assert 'data-key="archetype"' in body
    assert "Saved" not in body
    store.close()


def test_stats_strip_counts_the_library(tmp_path):
    store = Store(tmp_path / "banners.db")
    image = tmp_path / "creative.png"
    image.write_bytes(b"")
    _seed(store, "aa" * 32, "Delhi banner", city="Delhi", pincode="110001", path=str(image))
    _seed(store, "bb" * 32, "Mumbai banner", city="Mumbai", pincode="400050", path=str(image))

    destination = tmp_path / "dashboard.html"
    render(store.library(), destination, store.place_summaries())
    body = destination.read_text(encoding="utf-8")

    stats = body.split('<div class="stats">', 1)[1].split("</header>", 1)[0]
    assert "Creatives" in stats and ">2<" in stats
    assert "2 pincodes" in stats
    store.close()
