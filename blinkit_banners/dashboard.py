from __future__ import annotations

import html
import os
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

# Quick pills under the search field. "Occasions" is special; the rest are
# category names. Only pills that match at least one creative are rendered.
QUICK_PILLS = ("Occasions", "Home", "Chocolate", "Gifting", "Breakfast", "Skin Care", "Beauty")

DROPDOWNS = (
    ("brand", "Brand"),
    ("city", "City"),
    ("archetype", "Layout"),
    ("date", "Date"),
)

ARCHETYPE_LABELS = {
    "headline_sub_cta": "Headline + sub + CTA",
    "headline_sub": "Headline + sub",
    "headline_cta": "Headline + CTA",
    "headline": "Headline only",
    "image_only": "Image only",
}

STYLE = """
:root {
  --bg: #f4f5f7;
  --panel: #ffffff;
  --line: #e5e7ee;
  --ink: #14171f;
  --muted: #676f7e;
  --faint: #98a0af;
  --radius: 16px;
  --shadow: 0 1px 2px rgba(16,24,40,.05), 0 10px 30px rgba(16,24,40,.05);
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.55 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;
       -webkit-font-smoothing: antialiased; }
.pad { padding-left: 34px; padding-right: 34px; }
button { font: inherit; cursor: pointer; }

.eyebrow { font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--faint); font-weight: 600; }
h1 { margin: 6px 0 4px; font-size: 34px; letter-spacing: -0.025em; font-weight: 700; }
.sub { margin: 0; color: var(--muted); font-size: 14px; }

.masthead { padding-top: 30px; padding-bottom: 4px; }
.masthead-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
.actions { display: flex; gap: 10px; }
.btn { background: var(--panel); color: var(--ink); border: 1px solid var(--line); border-radius: 999px;
       padding: 9px 18px; font-size: 13px; font-weight: 550; box-shadow: 0 1px 2px rgba(16,24,40,.04);
       transition: border-color .15s, transform .15s; }
.btn:hover { border-color: #c9cedb; }
.btn:active { transform: translateY(1px); }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(178px, 1fr)); gap: 14px; margin: 22px 0 6px; }
.stat { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 15px 17px 16px;
        box-shadow: 0 1px 2px rgba(16,24,40,.04); }
.stat .k { font-size: 11px; letter-spacing: .09em; text-transform: uppercase; color: var(--faint); font-weight: 600; }
.stat .v { font-size: 25px; font-weight: 700; letter-spacing: -0.02em; margin-top: 7px; }
.stat .n { font-size: 12px; color: var(--muted); margin-top: 3px; }

.toolbar { margin: 22px 34px 0; padding: 18px 20px; background: var(--panel); border: 1px solid var(--line);
           border-radius: 18px; box-shadow: 0 1px 2px rgba(16,24,40,.04); position: sticky; top: 0; z-index: 5; }
.toolbar-top, .toolbar-bottom { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.toolbar-bottom { margin-top: 14px; padding-top: 14px; border-top: 1px solid #eef0f4; }
.search { flex: 1 1 280px; min-width: 220px; display: flex; align-items: center; gap: 10px;
          background: #f7f8fa; border: 1px solid var(--line); border-radius: 999px; padding: 0 16px; }
.search svg { flex: 0 0 auto; color: var(--faint); }
.search input { flex: 1; border: 0; background: transparent; padding: 11px 0; font-size: 13.5px;
                font-family: inherit; color: var(--ink); outline: none; min-width: 0; }
.pills { display: flex; flex-wrap: wrap; gap: 8px; }
.pill { border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 999px;
        padding: 8px 16px; font-size: 13px; font-weight: 550; transition: background .15s, color .15s, border-color .15s; }
.pill:hover { border-color: #c9cedb; }
.pill.on { background: #14171f; color: #fff; border-color: #14171f; }
select { appearance: none; background: var(--panel) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%2398a0af' d='M1.4 1.4 6 6l4.6-4.6L12 2.8 6 8.8 0 2.8z'/%3E%3C/svg%3E") no-repeat right 14px center;
         color: var(--ink); border: 1px solid var(--line); border-radius: 999px; padding: 9px 36px 9px 15px;
         font-size: 13px; font-family: inherit; }
select:focus { outline: none; border-color: #1f5eff; }
.count { margin-left: auto; color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }

.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; padding: 26px 34px 70px; }
@media (max-width: 1100px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
        overflow: hidden; display: flex; flex-direction: column; box-shadow: 0 1px 2px rgba(16,24,40,.04);
        transition: transform .16s, box-shadow .16s, border-color .16s; }
.card:hover { transform: translateY(-2px); box-shadow: var(--shadow); border-color: #d8dce6; }
.card img { width: 100%; display: block; background: #eef0f4; aspect-ratio: 1024 / 716; object-fit: contain; }
.body { padding: 16px 18px 18px; display: flex; flex-direction: column; gap: 8px; flex: 1; }
.headline { font-weight: 650; font-size: 15.5px; line-height: 1.35; letter-spacing: -0.01em; }
.headline.missing { color: var(--faint); font-weight: 400; font-style: italic; }
.subheadline { color: #4b5160; font-size: 13px; line-height: 1.45; }
.meta-block { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.where, .when { display: flex; align-items: flex-start; gap: 8px; font-size: 12.5px; color: var(--muted); line-height: 1.45; }
.where .icon, .when .icon { flex: 0 0 auto; line-height: 1.45; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: auto; padding-top: 10px; }
.chip { font-size: 11.5px; padding: 4px 10px; border-radius: 999px; font-weight: 550; border: 1px solid transparent; }
.chip.brand { background: #eaf1ff; border-color: #cfdcff; color: #1c4fd0; }
.chip.category { background: #eef8f1; border-color: #cde8d6; color: #1f7a45; }
.chip.occasion { background: #fdeef6; border-color: #f5d3e6; color: #9c2f72; }
.chip.ad { background: #fff4e5; border-color: #ffe0b0; color: #9a5b00; }
.chip.weak { opacity: .6; }
.empty { padding: 60px 34px; color: var(--muted); }
"""

SCRIPT = """
const cards = [...document.querySelectorAll('.card')];
const selects = [...document.querySelectorAll('select[data-key]')];
const search = document.getElementById('search');
const count = document.getElementById('count');
const pills = [...document.querySelectorAll('.pill')];
let quick = '';

function visible() { return cards.filter(card => card.style.display !== 'none'); }

function matchesQuick(card) {
  if (!quick) return true;
  if (quick === 'occasions') return Boolean(card.dataset.occasion);
  return (card.dataset.category || '') === quick;
}

function apply() {
  const term = search.value.trim().toLowerCase();
  let shown = 0;
  for (const card of cards) {
    const matchesFilters = selects.every(select => {
      if (!select.value) return true;
      const raw = card.dataset[select.dataset.key] || '';
      if (select.dataset.match === 'any') return raw.split('|').includes(select.value);
      return raw === select.value;
    });
    const matchesTerm = !term || card.dataset.text.includes(term);
    const show = matchesFilters && matchesTerm && matchesQuick(card);
    card.style.display = show ? '' : 'none';
    if (show) shown++;
  }
  count.textContent = shown + ' creative' + (shown === 1 ? '' : 's') + ' shown';
}

pills.forEach(pill => pill.addEventListener('click', () => {
  quick = pill.dataset.quick || '';
  pills.forEach(other => other.classList.toggle('on', other === pill));
  apply();
}));

document.getElementById('export').addEventListener('click', () => {
  const head = ['headline', 'brand', 'category', 'occasion', 'layout', 'cities', 'last_seen'];
  const lines = [head].concat(visible().map(card => [
    card.dataset.headline, card.dataset.brand, card.dataset.category,
    card.dataset.occasion, card.dataset.archetype,
    (card.dataset.city || '').split('|').join(' / '), card.dataset.date,
  ]));
  const csv = lines.map(row => row.map(v => '"' + String(v || '').replace(/"/g, '""') + '"').join(',')).join('\\n');
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
  link.download = 'blinkit-creatives.csv';
  link.click();
  URL.revokeObjectURL(link.href);
});

selects.forEach(select => select.addEventListener('change', apply));
search.addEventListener('input', apply);
apply();
"""


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(IST)


def _day(iso: str | None) -> str:
    stamp = _parse(iso)
    return stamp.strftime("%Y-%m-%d") if stamp else ""


def _when(first: str | None, last: str | None) -> str:
    start, end = _parse(first), _parse(last)
    if not start and not end:
        return ""
    if not end or not start or start.date() == end.date():
        day = start or end
        return f"Seen {day.day} {day.strftime('%b %Y')}"
    if start.year == end.year and start.month == end.month:
        return f"Seen {start.day}–{end.day} {end.strftime('%b %Y')}"
    if start.year == end.year:
        return f"Seen {start.day} {start.strftime('%b')} – {end.day} {end.strftime('%b %Y')}"
    return f"Seen {start.day} {start.strftime('%b %Y')} – {end.day} {end.strftime('%b %Y')}"


def _label(key: str, value: str) -> str:
    if key == "archetype":
        return ARCHETYPE_LABELS.get(value, value)
    if key == "date":
        stamp = _parse(f"{value}T00:00:00+05:30")
        return stamp.strftime("%d %b %Y") if stamp else value
    return value


def _options(rows: list, key: str) -> list[str]:
    counts = Counter(row[key] for row in rows if row[key])
    items = [value for value, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    if key == "date":
        return sorted(items, reverse=True)
    return items


def _cities(summary: dict) -> list[str]:
    raw = summary.get("cities") or ""
    return [part.strip() for part in raw.split(",") if part.strip()]


def _places(summary: dict) -> str:
    raw = summary.get("places") or ""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return " · ".join(parts)


def _first_seen(row: sqlite3.Row, summary: dict) -> str:
    return summary.get("first_at") or row["first_seen"] or ""


def _stat(key: str, value: str, note: str = "") -> str:
    note_html = f'<div class="n">{html.escape(note)}</div>' if note else ""
    return (
        f'<div class="stat"><div class="k">{html.escape(key)}</div>'
        f'<div class="v">{html.escape(value)}</div>{note_html}</div>'
    )


def _stats(rows: list[sqlite3.Row], summaries: dict[str, dict]) -> str:
    brands = {row["brand"] for row in rows if row["brand"]}
    occasions = {row["occasion"] for row in rows if row["occasion"]}
    cities: set[str] = set()
    places: set[str] = set()
    latest: datetime | None = None
    fresh = 0
    week_ago = datetime.now(IST) - timedelta(days=7)

    for row in rows:
        summary = summaries.get(row["phash"], {})
        cities.update(_cities(summary))
        places.update(part.strip() for part in (summary.get("places") or "").split(",") if part.strip())
        seen = _parse(summary.get("last_at") or row["last_seen"])
        if seen and (latest is None or seen > latest):
            latest = seen
        started = _parse(_first_seen(row, summary))
        if started and started >= week_ago:
            fresh += 1

    branded = sum(1 for row in rows if row["brand"])
    tagged = sum(1 for row in rows if row["occasion"])

    return "".join(
        (
            _stat("Creatives", str(len(rows)), f"+{fresh} this week" if fresh else "no new this week"),
            _stat("Brands", str(len(brands)), f"{branded} creatives branded"),
            _stat("Occasions", str(len(occasions)), f"{tagged} creatives tagged"),
            _stat("Cities", str(len(cities)), f"{len(places)} pincodes"),
            _stat(
                "Last scraped",
                latest.strftime("%d %b") if latest else "never",
                latest.strftime("%H:%M IST") if latest else "run the scraper",
            ),
        )
    )


def _quick_pills(rows: list[sqlite3.Row]) -> str:
    categories = {row["category"] for row in rows if row["category"]}
    has_occasion = any(row["occasion"] for row in rows)
    buttons = ['<button class="pill on" data-quick="">All</button>']
    for label in QUICK_PILLS:
        if label == "Occasions":
            if not has_occasion:
                continue
            value = "occasions"
        elif label not in categories:
            continue
        else:
            value = label
        buttons.append(
            f'<button class="pill" data-quick="{html.escape(value)}">{html.escape(label)}</button>'
        )
    return f'<div class="pills">{"".join(buttons)}</div>'


def _card(row: sqlite3.Row, image_src: str, summary: dict) -> str:
    headline = row["headline"] or ""
    subheadline = row["subheadline"] or ""
    brand = row["brand"] or ""
    category = row["category"] or ""
    places = _places(summary)
    cities = _cities(summary)
    first_at = summary.get("first_at") or row["first_seen"]
    last_at = summary.get("last_at") or row["last_seen"]
    when = _when(first_at, last_at)
    searchable = " ".join(
        filter(None, [headline, subheadline, brand, category, row["occasion"], row["ocr_text"], places])
    ).lower()

    title = html.escape(headline) if headline else "no headline detected"
    title_class = "headline" if headline else "headline missing"
    sub_html = f'<div class="subheadline">{html.escape(subheadline)}</div>' if subheadline else ""
    where_html = (
        f'<div class="where"><span class="icon" aria-hidden="true">📍</span>'
        f"<span>{html.escape(places)}</span></div>"
        if places
        else ""
    )
    when_html = (
        f'<div class="when"><span class="icon" aria-hidden="true">📅</span>'
        f"<span>{html.escape(when)}</span></div>"
        if when
        else ""
    )
    meta = ""
    if where_html or when_html:
        meta = f'<div class="meta-block">{where_html}{when_html}</div>'

    chips = []
    if brand:
        weak = " weak" if row["brand_source"] == "product" else ""
        title_attr = f'{row["brand_source"]} match, confidence {row["brand_confidence"]:.2f}'
        chips.append(
            f'<span class="chip brand{weak}" title="{html.escape(title_attr)}">{html.escape(brand)}</span>'
        )
    if category:
        chips.append(f'<span class="chip category">{html.escape(category)}</span>')
    if row["occasion"]:
        chips.append(f'<span class="chip occasion">{html.escape(row["occasion"])}</span>')
    if row["has_ad_badge"]:
        chips.append('<span class="chip ad">Ad</span>')
    chips_html = f'<div class="chips">{"".join(chips)}</div>' if chips else ""

    data = {
        "phash": row["phash"],
        "headline": headline,
        "brand": brand,
        "category": category,
        "occasion": row["occasion"] or "",
        "archetype": row["archetype"] or "",
        "background": row["background"] or "",
        "city": "|".join(cities),
        "date": _day(last_at),
        "text": searchable,
    }
    attributes = " ".join(f'data-{key}="{html.escape(value)}"' for key, value in data.items())

    return f"""<article class="card" id="c-{html.escape(row["phash"])}" {attributes}>
  <img loading="lazy" src="{html.escape(image_src)}" alt="{html.escape(headline or row["phash"][:12])}">
  <div class="body">
    <div class="{title_class}">{title}</div>
    {sub_html}
    {meta}
    {chips_html}
  </div>
</article>"""


def _asset_src(row: sqlite3.Row, destination: Path, asset_dir: Path | None) -> str:
    """Where the page should point for this creative's image.

    Without `asset_dir` the page links straight at the library on disk, which
    is what the local file:// dashboard wants. With it, each image is copied
    next to the page so the whole thing can be uploaded as one static site —
    filenames are content hashes, so a copy is only ever made once.
    """
    origin = Path(row["file_path"])
    target = origin
    if asset_dir is not None:
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / origin.name
        if origin.exists() and not target.exists():
            shutil.copy2(origin, target)
    relative = os.path.relpath(target.resolve(), destination.parent.resolve())
    return relative.replace(os.sep, "/")


def render(
    rows: list[sqlite3.Row],
    destination: Path,
    summaries: dict[str, dict],
    asset_dir: Path | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    cards = []
    filter_rows = []
    for row in rows:
        summary = summaries.get(row["phash"], {})
        cards.append(_card(row, _asset_src(row, destination, asset_dir), summary))
        last_at = summary.get("last_at") or row["last_seen"]
        for city in _cities(summary) or [""]:
            filter_rows.append(
                {
                    "city": city,
                    "date": _day(last_at),
                    "brand": row["brand"],
                    "archetype": row["archetype"],
                    "background": row["background"],
                }
            )

    dropdowns = []
    for key, label in DROPDOWNS:
        extra = ' data-match="any"' if key == "city" else ""
        options = "".join(
            f'<option value="{html.escape(value)}">{html.escape(_label(key, value))}</option>'
            for value in _options(filter_rows, key)
        )
        dropdowns.append(
            f'<select data-key="{key}"{extra}><option value="">{label}</option>{options}</select>'
        )

    search_icon = (
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/>'
        '<path d="M20 20l-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        "</svg>"
    )
    toolbar = f"""<div class="toolbar">
  <div class="toolbar-top">
    <label class="search">{search_icon}
      <input id="search" type="search" placeholder="Search headline, copy, brand, category\u2026">
    </label>
    {_quick_pills(rows)}
  </div>
  <div class="toolbar-bottom">
    {"".join(dropdowns)}
    <span class="count" id="count"></span>
  </div>
</div>"""

    body = "".join(cards) or '<p class="empty">No creatives yet. Run <code>run</code> first.</p>'

    destination.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blinkit creative library</title>
<style>{STYLE}</style>
</head>
<body>
<header class="masthead pad">
  <div class="eyebrow">Blinkit &middot; Creative intelligence</div>
  <div class="masthead-row">
    <div>
      <h1>Creative Library</h1>
      <p class="sub">Browse what is live, what just landed, and what is worth keeping.</p>
    </div>
    <div class="actions">
      <button class="btn" id="export">Export view</button>
    </div>
  </div>
  <div class="stats">{_stats(rows, summaries)}</div>
</header>
{toolbar}
<main class="grid">{body}</main>
<script>{SCRIPT}</script>
</body>
</html>
""",
        encoding="utf-8",
    )
