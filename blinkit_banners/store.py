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
    sighting_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sightings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phash           TEXT NOT NULL REFERENCES creatives(phash),
    location        TEXT NOT NULL,
    surface         TEXT NOT NULL,
    slot_index      INTEGER NOT NULL,
    captured_at     TEXT NOT NULL,
    device_serial   TEXT,
    screenshot_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_sightings_phash ON sightings(phash);
CREATE INDEX IF NOT EXISTS idx_sightings_time ON sightings(captured_at);
"""


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
        self.conn.commit()
        self._hashes: list[str] = [r["phash"] for r in self.conn.execute("SELECT phash FROM creatives")]

    def close(self) -> None:
        self.conn.close()

    def match(self, phash: str, max_distance: int) -> str | None:
        """Return an existing creative hash within `max_distance`, nearest first."""
        best: tuple[int, str] | None = None
        for known in self._hashes:
            if len(known) != len(phash):
                continue
            distance = _hamming(phash, known)
            if distance <= max_distance and (best is None or distance < best[0]):
                best = (distance, known)
        return best[1] if best else None

    def add_creative(self, phash: str, file_path: str, width: int, height: int) -> None:
        now = _now()
        self.conn.execute(
            "INSERT OR IGNORE INTO creatives (phash, file_path, width, height, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (phash, file_path, width, height, now, now),
        )
        self._hashes.append(phash)
        self.conn.commit()

    def add_sighting(
        self,
        phash: str,
        location: str,
        surface: str,
        slot_index: int,
        device_serial: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        now = _now()
        self.conn.execute(
            "INSERT INTO sightings (phash, location, surface, slot_index, captured_at, device_serial, screenshot_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (phash, location, surface, slot_index, now, device_serial, screenshot_path),
        )
        self.conn.execute(
            "UPDATE creatives SET last_seen = ?, sighting_count = sighting_count + 1 WHERE phash = ?",
            (now, phash),
        )
        self.conn.commit()

    def creative_path(self, phash: str) -> str | None:
        row = self.conn.execute("SELECT file_path FROM creatives WHERE phash = ?", (phash,)).fetchone()
        return row["file_path"] if row else None

    def stats(self) -> dict[str, int]:
        creatives = self.conn.execute("SELECT COUNT(*) AS n FROM creatives").fetchone()["n"]
        sightings = self.conn.execute("SELECT COUNT(*) AS n FROM sightings").fetchone()["n"]
        return {"creatives": creatives, "sightings": sightings}
