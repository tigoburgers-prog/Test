"""Shared fixtures: isolated data folder and synthetic traffic videos."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FPS = 25.0
W, H = 640, 360
CAR = (56, 28)


def _cars(duration: float) -> list[dict]:
    """Lane 1 (y=110): left→right, never stops.
    Lane 2 (y=250): right→left; a 'red light' at x=200 holds cars from t=3 to t=8 → queue."""
    cars = []
    for i, t0 in enumerate(np.arange(0.2, duration - 3, 1.6)):
        cars.append({"lane": 1, "t0": float(t0), "color": (40 + 30 * (i % 5), 90, 200)})
    for i, t0 in enumerate(np.arange(0.5, duration - 4, 1.3)):
        cars.append({"lane": 2, "t0": float(t0), "color": (60, 180, 40 + 35 * (i % 5))})
    return cars


def simulate(cars: list[dict], duration: float) -> list[list[tuple[int, float, float]]]:
    """Per-frame (car, x, y). Lane 2 has a stop line at x=200 that is 'red' from t=3 s to t=8 s;
    cars keep a minimum gap, so a queue forms and dissolves."""
    speed, stop_x, gap = 140.0, 200.0, CAR[0] + 14
    dt = 1.0 / FPS
    state: dict[int, float] = {}
    frames = []
    for f in range(int(duration * FPS)):
        t = f * dt
        out = []
        for i, c in enumerate(cars):
            if c["lane"] != 1 or t < c["t0"]:
                continue
            x = state.get(i, -CAR[0]) + speed * dt
            state[i] = x
            if x < W + CAR[0]:
                out.append((i, x, 110.0))
        ahead_x = None
        for i, c in sorted(enumerate(cars), key=lambda ic: ic[1]["t0"]):
            if c["lane"] != 2 or t < c["t0"]:
                continue
            x = state.get(i, W + CAR[0])
            nx = x - speed * dt
            if 3.0 <= t < 8.0 and x >= stop_x:
                nx = max(nx, stop_x)
            if ahead_x is not None:
                nx = max(nx, ahead_x + gap)
            state[i] = nx
            ahead_x = nx
            if nx > -CAR[0]:
                out.append((i, nx, 250.0))
        frames.append(out)
    return frames


def make_video(path: Path, duration: float = 14.0) -> Path:
    rng = np.random.default_rng(0)
    bg = rng.integers(95, 125, (H, W, 3), dtype=np.uint8)
    cv2.line(bg, (0, 180), (W, 180), (230, 230, 230), 2)
    cars = _cars(duration)
    colors = {i: c["color"] for i, c in enumerate(cars)}
    sim = simulate(cars, duration)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    assert vw.isOpened()
    for positions in sim:
        img = bg.copy()
        for cid, x, y in positions:
            x0, y0 = int(x - CAR[0] / 2), int(y - CAR[1] / 2)
            cv2.rectangle(img, (x0, y0), (x0 + CAR[0], y0 + CAR[1]), colors[cid], -1)
            cv2.rectangle(img, (x0 + 10, y0 + 5), (x0 + 22, y0 + CAR[1] - 5), (30, 30, 30), -1)
        vw.write(img)
    vw.release()
    return path


def expected_counts(duration: float = 14.0) -> tuple[int, int]:
    cars = _cars(duration)
    return sum(c["lane"] == 1 for c in cars), sum(c["lane"] == 2 for c in cars)


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("video") / "verkeer.mp4")


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("TRAFFIC_SNAPSHOT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """Created first for the whole session, exactly like the real application does."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - missing system GL libs
        pytest.skip(f"Qt not available: {exc}")
    app = QApplication.instance() or QApplication([])
    from traffic_snapshot.ui.theme import apply_theme

    apply_theme(app)
    yield app
    # Tear widgets down while Qt is still alive (avoids crashes at interpreter exit).
    import gc

    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    gc.collect()
