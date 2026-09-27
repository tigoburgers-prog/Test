"""SQLite store for raw analysis data and resume checkpoints.

The frame loop writes tracked observations in batches. Each batch is
committed in the *same transaction* as a checkpoint containing the tracker
and sampler state, so after a crash or a stop the database always describes a
consistent point from which the analysis can continue.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
    frame INTEGER NOT NULL,
    t REAL NOT NULL,
    track INTEGER NOT NULL,
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
    cat INTEGER NOT NULL,
    score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_frame ON observations(frame);
CREATE TABLE IF NOT EXISTS frames (
    frame INTEGER PRIMARY KEY,
    t REAL NOT NULL,
    n_det INTEGER NOT NULL,
    stride INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_log (
    frame INTEGER NOT NULL,
    t REAL NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_frame INTEGER NOT NULL,
    state BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class Checkpoint:
    next_frame: int
    state: dict[str, Any]


class AnalysisStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", SCHEMA_VERSION)
            self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> AnalysisStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # ------------------------------------------------------------------ writes
    def commit_batch(
        self,
        observations: Iterable[tuple],
        frames: Iterable[tuple],
        log_rows: Iterable[tuple],
        checkpoint: Checkpoint,
    ) -> None:
        """Atomically persist a batch plus the checkpoint that follows it."""
        with self.conn:  # one transaction
            self.conn.executemany(
                "INSERT INTO observations(frame, t, track, x1, y1, x2, y2, cat, score) VALUES (?,?,?,?,?,?,?,?,?)",
                observations,
            )
            self.conn.executemany("INSERT OR REPLACE INTO frames(frame, t, n_det, stride) VALUES (?,?,?,?)", frames)
            self.conn.executemany("INSERT INTO strategy_log(frame, t, event, detail) VALUES (?,?,?,?)", log_rows)
            self.conn.execute(
                "INSERT OR REPLACE INTO checkpoint(id, next_frame, state, updated_at) VALUES (1, ?, ?, datetime('now'))",
                (checkpoint.next_frame, pickle.dumps(checkpoint.state, protocol=pickle.HIGHEST_PROTOCOL)),
            )

    def load_checkpoint(self) -> Checkpoint | None:
        row = self.conn.execute("SELECT next_frame, state FROM checkpoint WHERE id = 1").fetchone()
        if not row:
            return None
        try:
            state = pickle.loads(row[1])
        except Exception:
            return None
        # Defensive: drop anything written beyond the checkpoint.
        with self.conn:
            self.conn.execute("DELETE FROM observations WHERE frame >= ?", (row[0],))
            self.conn.execute("DELETE FROM frames WHERE frame >= ?", (row[0],))
        return Checkpoint(row[0], state)

    # ------------------------------------------------------------------ reads
    def observations_array(self) -> dict[str, np.ndarray]:
        cur = self.conn.execute(
            "SELECT frame, t, track, x1, y1, x2, y2, cat, score FROM observations ORDER BY track, frame"
        )
        rows = cur.fetchall()
        if not rows:
            arr = np.zeros((0, 9), np.float64)
        else:
            arr = np.asarray(rows, dtype=np.float64)
        return {
            "frame": arr[:, 0].astype(np.int64),
            "t": arr[:, 1],
            "track": arr[:, 2].astype(np.int64),
            "box": arr[:, 3:7].astype(np.float32),
            "cat": arr[:, 7].astype(np.int16),
            "score": arr[:, 8].astype(np.float32),
        }

    def frames_array(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        rows = self.conn.execute("SELECT frame, t, stride FROM frames ORDER BY frame").fetchall()
        if not rows:
            return np.zeros(0, np.int64), np.zeros(0), np.zeros(0, np.int64)
        a = np.asarray(rows, dtype=np.float64)
        return a[:, 0].astype(np.int64), a[:, 1], a[:, 2].astype(np.int64)

    def strategy_log(self) -> list[dict]:
        rows = self.conn.execute("SELECT frame, t, event, detail FROM strategy_log ORDER BY frame").fetchall()
        return [{"frame": r[0], "t": r[1], "event": r[2], "detail": r[3]} for r in rows]
