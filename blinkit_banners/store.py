from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS creatives (
    phash           TEXT PRIMARY KEY,
    file_path       TEXT NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    sighting_count  INTEGER NOT NULL DEFAULT 0,
    -- Captured from a slide that was still changing between two reads, i.e. a
    -- frame of a video ad rather than a static banner.
    is_video        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sightings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phash           TEXT NOT NULL REFERENCES creatives(phash),
    location        TEXT NOT NULL,
    city            TEXT,
    pincode         TEXT,
    surface         TEXT NOT NULL,
    slot_index      INTEGER NOT NULL,
    captured_at     TEXT NOT NULL,
    device_serial   TEXT,
    screenshot_path TEXT
);

-- Brands are not a fixed list. Every wordmark the analyser reads off a creative
-- lands here and is available to match against on the next run, so coverage
-- grows with the archive instead of with hand-maintained config.
-- Usage counts are deliberately not stored here; they are derived from
-- `attributes` so that re-analysing the library cannot inflate them.
CREATE TABLE IF NOT EXISTS discovered_brands (
    slug        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    origin      TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attributes (
    phash            TEXT PRIMARY KEY REFERENCES creatives(phash),
    headline         TEXT,
    subheadline      TEXT,
    cta              TEXT,
    has_ad_badge     INTEGER NOT NULL DEFAULT 0,
    archetype        TEXT,
    text_side        TEXT,
    brand            TEXT,
    brand_source     TEXT,
    brand_confidence REAL,
    category         TEXT,
    occasion         TEXT,
    dominant_colour  TEXT,
    palette          TEXT,
    background       TEXT,
    ocr_text         TEXT,
    analysed_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sightings_phash ON sightings(phash);
CREATE INDEX IF NOT EXISTS idx_sightings_time ON sightings(captured_at);
CREATE INDEX IF NOT EXISTS idx_attributes_brand ON attributes(brand);
"""

ATTRIBUTE_FIELDS = (
    "headline",
    "subheadline",
    "cta",
    "has_ad_badge",
    "archetype",
    "text_side",
    "brand",
    "brand_source",
    "brand_confidence",
    "category",
    "occasion",
    "dominant_colour",
    "palette",
    "background",
    "ocr_text",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()
        self._hashes: list[tuple[str, bool]] = [
            (row["phash"], bool(row["is_video"]))
            for row in self.conn.execute("SELECT phash, is_video FROM creatives")
        ]

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        for table, column, ddl in (
            ("sightings", "city", "TEXT"),
            ("sightings", "pincode", "TEXT"),
            ("creatives", "is_video", "INTEGER NOT NULL DEFAULT 0"),
        ):
            existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self.conn.close()

    def match(
        self,
        phash: str,
        max_distance: int,
        *,
        animated: bool = False,
        animated_distance: int | None = None,
    ) -> str | None:
        """Return an existing creative hash for this image, nearest first.

        Two passes. The strict one runs against the whole library, so a banner
        always finds its own earlier render. Only if that fails, and this frame
        came off a video, does the loose pass run — and then only against other
        video frames, which is what lets the frames of one spot collapse into a
        single creative without ever merging two static banners.
        """
        strict = self._nearest(phash, max_distance, video_only=False)
        if strict or not animated or animated_distance is None:
            return strict
        return self._nearest(phash, animated_distance, video_only=True)

    def _nearest(self, phash: str, max_distance: int, *, video_only: bool) -> str | None:
        best: tuple[int, str] | None = None
        for known, is_video in self._hashes:
            if len(known) != len(phash) or (video_only and not is_video):
                continue
            distance = _hamming(phash, known)
            if distance <= max_distance and (best is None or distance < best[0]):
                best = (distance, known)
        return best[1] if best else None

    def add_creative(
        self, phash: str, file_path: str, width: int, height: int, is_video: bool = False
    ) -> None:
        now = _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO creatives"
            " (phash, file_path, width, height, first_seen, last_seen, is_video)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (phash, file_path, width, height, now, now, int(is_video)),
        )
        self._hashes.append((phash, is_video))
        self.conn.commit()

    def add_sighting(
        self,
        phash: str,
        location: str,
        surface: str,
        slot_index: int,
        device_serial: str | None = None,
        screenshot_path: str | None = None,
        city: str = "",
        pincode: str = "",
    ) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO sightings"
            " (phash, location, city, pincode, surface, slot_index, captured_at, device_serial, screenshot_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (phash, location, city, pincode, surface, slot_index, now, device_serial, screenshot_path),
        )
        self.conn.execute(
            "UPDATE creatives SET last_seen = ?, sighting_count = sighting_count + 1 WHERE phash = ?",
            (now, phash),
        )
        self.conn.commit()

    def delete_creative(self, phash: str) -> Path | None:
        """Drop a creative and its sightings/attributes. Returns the file path if known."""
        row = self.conn.execute("SELECT file_path FROM creatives WHERE phash = ?", (phash,)).fetchone()
        self.conn.execute("DELETE FROM attributes WHERE phash = ?", (phash,))
        self.conn.execute("DELETE FROM sightings WHERE phash = ?", (phash,))
        self.conn.execute("DELETE FROM creatives WHERE phash = ?", (phash,))
        self._hashes = [entry for entry in self._hashes if entry[0] != phash]
        self.conn.commit()
        return Path(row["file_path"]) if row else None

    def creative_path(self, phash: str) -> str | None:
        row = self.conn.execute("SELECT file_path FROM creatives WHERE phash = ?", (phash,)).fetchone()
        return row["file_path"] if row else None

    def creatives(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM creatives ORDER BY first_seen, phash"))

    def analysed_hashes(self) -> set[str]:
        return {row["phash"] for row in self.conn.execute("SELECT phash FROM attributes")}

    def save_attributes(self, phash: str, values: dict) -> None:
        columns = ", ".join(("phash", *ATTRIBUTE_FIELDS, "analysed_at"))
        placeholders = ", ".join("?" * (len(ATTRIBUTE_FIELDS) + 2))
        row = [phash, *(values.get(field) for field in ATTRIBUTE_FIELDS), _now()]
        self.conn.execute(f"INSERT OR REPLACE INTO attributes ({columns}) VALUES ({placeholders})", row)
        self.conn.commit()

    def library(self) -> list[sqlite3.Row]:
        """Creatives joined with their attributes, for reporting and the dashboard."""
        return list(
            self.conn.execute(
                "SELECT c.*, a.* FROM creatives c LEFT JOIN attributes a ON a.phash = c.phash"
                " ORDER BY a.brand IS NULL, a.brand, c.first_seen"
            )
        )

    def known_brands(self) -> list[sqlite3.Row]:
        """Learned brands with a live count of the creatives using each one."""
        return list(
            self.conn.execute(
                "SELECT b.*, (SELECT COUNT(*) FROM attributes a WHERE a.brand = b.name) AS creatives"
                " FROM discovered_brands b ORDER BY creatives DESC, b.name"
            )
        )

    def remember_brand(self, slug: str, name: str, category: str, origin: str) -> None:
        """Record a brand read off a creative, or refresh one already known."""
        now = _now()
        self.conn.execute(
            "INSERT INTO discovered_brands (slug, name, category, origin, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET"
            "   last_seen = excluded.last_seen,"
            "   category = COALESCE(NULLIF(discovered_brands.category, ''), excluded.category)",
            (slug, name, category, origin, now, now),
        )
        self.conn.commit()

    def forget_brands(self) -> None:
        self.conn.execute("DELETE FROM discovered_brands")
        self.conn.commit()

    def sightings_for(self, phash: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT location, city, pincode, captured_at FROM sightings"
                " WHERE phash = ? ORDER BY captured_at",
                (phash,),
            )
        )

    def place_summaries(self) -> dict[str, dict]:
        """Per creative: first/last scrape time and the cities it has appeared in."""
        rows = self.conn.execute(
            "SELECT phash,"
            "       MIN(captured_at) AS first_at,"
            "       MAX(captured_at) AS last_at,"
            "       GROUP_CONCAT(DISTINCT CASE"
            "         WHEN COALESCE(city, '') != '' AND COALESCE(pincode, '') != ''"
            "           THEN city || ' ' || pincode"
            "         WHEN COALESCE(city, '') != '' THEN city"
            "         ELSE location"
            "       END) AS places,"
            "       GROUP_CONCAT(DISTINCT CASE"
            "         WHEN COALESCE(city, '') != '' THEN city ELSE location"
            "       END) AS cities"
            " FROM sightings GROUP BY phash"
        )
        return {row["phash"]: dict(row) for row in rows}

    def stats(self) -> dict[str, int]:
        creatives = self.conn.execute("SELECT COUNT(*) AS n FROM creatives").fetchone()["n"]
        sightings = self.conn.execute("SELECT COUNT(*) AS n FROM sightings").fetchone()["n"]
        return {"creatives": creatives, "sightings": sightings}
