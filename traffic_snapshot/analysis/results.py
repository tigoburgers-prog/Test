"""Stored analysis results and the query layer used by dashboard and reports.

Results are computed once by the pipeline and cached in ``results/``. At
project open they are loaded into memory; every dashboard view (time range,
zone, heatmap type) is then a fast vectorised numpy query — nothing is ever
re-analysed when switching views.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from traffic_snapshot.analysis.categories import Category
from traffic_snapshot.analysis.postprocess import STATE_LOW, STATE_STATIONARY
from traffic_snapshot.core.formatting import fmt_number

GRID_COLS = 32

HEATMAP_KINDS = {
    "density": ("Voertuigdichtheid", "Gemiddeld aantal voertuigen tegelijk aanwezig per gebied"),
    "movement": ("Relatieve beweging", "Warm-koud patroon: warm = relatief meer beweging, koud = relatief minder"),
    "variability": ("Bewegingsspreiding", "Hoe wisselend de beweging per gebied is (hoog = sterk wisselend)"),
    "stagnation": ("Stilstand", "Gemiddeld aantal stilstaande voertuigen per gebied"),
}

EVENT_TYPES = {
    "peak": ("Piek in verkeersintensiteit", "#2563EB"),
    "stagnation": ("Stagnatie", "#DC2626"),
    "queue": ("Wachtrij-indicatie", "#EA580C"),
    "long_stop": ("Langdurige stilstand", "#9333EA"),
    "variability": ("Sterk wisselende beweging", "#0891B2"),
}


def choose_bin_seconds(duration: float, max_bins: int = 120) -> float:
    for b in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if duration / b <= max_bins:
            return float(b)
    return 900.0


@dataclass(frozen=True)
class Region:
    """Normalised rectangle (0..1 image coordinates)."""

    x0: float
    y0: float
    x1: float
    y1: float
    name: str = ""

    def contains(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return (x >= self.x0) & (x < self.x1) & (y >= self.y0) & (y < self.y1)


class AnalysisResults:
    def __init__(self, obs: dict, tracks: dict, summary: dict, results_dir: Path | None = None) -> None:
        self.obs = obs
        self.tracks = tracks
        self.summary = summary
        self.results_dir = results_dir
        self.duration = float(summary["video"]["duration_s"])
        self.fps = float(summary["video"]["fps"])
        self.width = int(summary["video"]["width"])
        self.height = int(summary["video"]["height"])
        self.grid_cols = GRID_COLS
        self.grid_rows = max(6, int(round(GRID_COLS * self.height / max(self.width, 1))))
        self.bin_s = choose_bin_seconds(self.duration)
        self.directions = summary.get("directions", [])
        self.events = summary.get("events", [])
        self._background: np.ndarray | None = None
        self.processed_frames = obs.get("processed_frames", np.zeros(0, np.int64))
        self._frame_starts = None

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, results_dir: Path) -> AnalysisResults:
        results_dir = Path(results_dir)
        summary = json.loads((results_dir / "summary.json").read_text(encoding="utf-8"))
        with np.load(results_dir / "observations.npz") as z:
            obs = {k: z[k] for k in z.files}
        with np.load(results_dir / "tracks.npz") as z:
            tracks = {k: z[k] for k in z.files}
        return cls(obs, tracks, summary, results_dir)

    def save(self, results_dir: Path) -> None:
        results_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(results_dir / "observations.npz", **self.obs)
        np.savez_compressed(results_dir / "tracks.npz", **self.tracks)
        tmp = results_dir / "summary.json.tmp"
        tmp.write_text(json.dumps(self.summary, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(results_dir / "summary.json")

    @property
    def background(self) -> np.ndarray:
        if self._background is None:
            img = None
            if self.results_dir and (self.results_dir / "background.jpg").is_file():
                data = np.fromfile(str(self.results_dir / "background.jpg"), np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                img = np.full((self.height, self.width, 3), 60, np.uint8)
            self._background = img
        return self._background

    # ------------------------------------------------------------------ playback lookup
    def frame_slice(self, frame: int) -> slice:
        """Observations of the processed frame at or just before ``frame``."""
        fr = self.obs["frame"]
        pf = self.processed_frames
        if len(pf) == 0:
            return slice(0, 0)
        i = np.searchsorted(pf, frame, "right") - 1
        if i < 0:
            return slice(0, 0)
        f = pf[i]
        return slice(int(np.searchsorted(fr, f, "left")), int(np.searchsorted(fr, f, "right")))

    def neighbour_frames(self, frame: int) -> tuple[int | None, int | None]:
        pf = self.processed_frames
        if len(pf) == 0:
            return None, None
        i = np.searchsorted(pf, frame, "right") - 1
        a = int(pf[i]) if i >= 0 else None
        b = int(pf[i + 1]) if i + 1 < len(pf) else None
        return a, b

    def slice_for_processed(self, f: int) -> slice:
        fr = self.obs["frame"]
        return slice(int(np.searchsorted(fr, f, "left")), int(np.searchsorted(fr, f, "right")))

    def track_history(self, track_ids: np.ndarray, t_now: float, span_s: float = 3.0) -> dict[int, np.ndarray]:
        """Recent ground-point path per track (normalised coords) for tracking trails."""
        o = self.obs
        m = (o["t"] <= t_now) & (o["t"] >= t_now - span_s) & np.isin(o["track"], track_ids)
        idx = np.flatnonzero(m)
        out: dict[int, np.ndarray] = {}
        for tid in np.unique(o["track"][idx]):
            sel = idx[o["track"][idx] == tid]
            b = o["box"][sel]
            out[int(tid)] = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
        return out

    # ------------------------------------------------------------------ selection
    def select(self, t0: float | None = None, t1: float | None = None, region: Region | None = None) -> Selection:
        return Selection(self, t0 or 0.0, self.duration if t1 is None else t1, region)

    def cell_region(self, col: int, row: int) -> Region:
        return Region(col / self.grid_cols, row / self.grid_rows, (col + 1) / self.grid_cols,
                      (row + 1) / self.grid_rows, f"Gebied {chr(65 + min(col // 4, 25))}{row // 2 + 1}")

    def coarse_region_at(self, x: float, y: float) -> Region:
        """A clickable analysis area (≈ 1/8 × 1/6 of the image) around a point."""
        cols, rows = 8, 6
        c = min(cols - 1, max(0, int(x * cols)))
        r = min(rows - 1, max(0, int(y * rows)))
        return Region(c / cols, r / rows, (c + 1) / cols, (r + 1) / rows, f"Gebied {chr(65 + c)}{r + 1}")


class Selection:
    """A time range and/or zone of the results. All metrics derive from here."""

    def __init__(self, res: AnalysisResults, t0: float, t1: float, region: Region | None) -> None:
        self.res = res
        self.t0 = max(0.0, t0)
        self.t1 = min(res.duration, max(t1, self.t0 + 1e-6))
        self.region = region
        o = res.obs
        m = (o["t"] >= self.t0) & (o["t"] <= self.t1)
        if region is not None:
            m &= region.contains(o["gx"], o["gy"])
        self.mask = m
        self.idx = np.flatnonzero(m)
        tr = o["track_row"][self.idx]
        self.track_rows = np.unique(tr)
        T = res.tracks
        self.vehicle_rows = self.track_rows[T["cat"][self.track_rows] != Category.PEDESTRIAN] if len(self.track_rows) else self.track_rows
        self.movement_rows = self.vehicle_rows[T["moving"][self.vehicle_rows]] if len(self.vehicle_rows) else self.vehicle_rows

    @property
    def duration(self) -> float:
        return self.t1 - self.t0

    @property
    def is_full(self) -> bool:
        return self.region is None and self.t0 <= 0.01 and self.t1 >= self.res.duration - 0.01

    def describe(self) -> str:
        from traffic_snapshot.core.formatting import fmt_duration

        parts = []
        if self.t0 <= 0.01 and self.t1 >= self.res.duration - 0.01:
            parts.append("volledige video")
        else:
            parts.append(f"{fmt_duration(self.t0)} – {fmt_duration(self.t1)}")
        if self.region is not None:
            parts.append(self.region.name or "geselecteerde zone")
        return ", ".join(parts)

    def _o(self, key: str) -> np.ndarray:
        return self.res.obs[key][self.idx]

    def _vehicle_obs(self) -> np.ndarray:
        return self._o("cat") != Category.PEDESTRIAN

    # ------------------------------------------------------------------ counts
    def counts_by_category(self, movements_only: bool = True) -> dict[int, int]:
        rows = self.movement_rows if movements_only else self.track_rows
        cats = self.res.tracks["cat"][rows]
        return {int(c): int((cats == c).sum()) for c in np.unique(cats)}

    def counts_by_direction(self) -> dict[int, int]:
        d = self.res.tracks["direction"][self.movement_rows]
        return {int(k): int((d == k).sum()) for k in np.unique(d)}

    def pedestrian_count(self) -> int:
        T = self.res.tracks
        rows = self.track_rows
        return int(((T["cat"][rows] == Category.PEDESTRIAN) & T["moving"][rows]).sum())

    def static_count(self) -> int:
        T = self.res.tracks
        return int(T["static"][self.vehicle_rows].sum())

    # ------------------------------------------------------------------ time series
    def time_bins(self, bin_s: float | None = None) -> np.ndarray:
        b = bin_s or self.res.bin_s
        start = np.floor(self.t0 / b) * b
        return np.arange(start, self.t1 + 1e-9, b)

    def _bin_index(self, t: np.ndarray, edges: np.ndarray) -> np.ndarray:
        return np.clip(np.searchsorted(edges, t, "right") - 1, 0, max(0, len(edges) - 1))

    def intensity_series(self, bin_s: float | None = None) -> tuple[np.ndarray, dict[int, np.ndarray]]:
        """New traffic movements per bin (by first appearance in the selection), per category."""
        edges = self.time_bins(bin_s)
        nb = len(edges)
        o = self.res.obs
        first_t: dict[int, float] = {}
        tr = o["track_row"][self.idx]
        tt = o["t"][self.idx]
        if len(tr):
            order = np.lexsort((tt, tr))
            tr_s, tt_s = tr[order], tt[order]
            firsts = np.concatenate([[True], tr_s[1:] != tr_s[:-1]])
            first_t = dict(zip(tr_s[firsts].tolist(), tt_s[firsts].tolist()))
        out: dict[int, np.ndarray] = {}
        cats = self.res.tracks["cat"]
        for row in self.movement_rows:
            c = int(cats[row])
            arr = out.setdefault(c, np.zeros(nb))
            arr[self._bin_index(np.array([first_t[int(row)]]), edges)[0]] += 1
        return edges, out

    def presence_series(self, bin_s: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Average vehicles present, stationary and in queue-indication per bin."""
        edges = self.time_bins(bin_s)
        b = edges[1] - edges[0] if len(edges) > 1 else (bin_s or self.res.bin_s)
        v = self._vehicle_obs() & ~self._o("static")
        t = self._o("t")[v]
        dur = self._o("dur")[v]
        bi = self._bin_index(t, edges)
        span = np.full(len(edges), b)
        span[-1] = max(1e-6, min(b, self.t1 - edges[-1])) if len(edges) else b
        span[0] = max(1e-6, min(span[0], edges[0] + b - self.t0))
        present = np.bincount(bi, weights=dur, minlength=len(edges)) / span
        st = self._o("state")[v] == STATE_STATIONARY
        stationary = np.bincount(bi, weights=dur * st, minlength=len(edges)) / span
        q = self._o("queue")[v]
        queue = np.bincount(bi, weights=dur * q, minlength=len(edges)) / span
        return edges, present, stationary, queue

    def movement_series(self, bin_s: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Median relative movement and spread (IQR/median) of moving vehicles per bin."""
        edges = self.time_bins(bin_s)
        v = self._vehicle_obs() & ~self._o("static") & (self._o("state") != STATE_STATIONARY)
        t, rel = self._o("t")[v], self._o("rel")[v]
        bi = self._bin_index(t, edges)
        med = np.full(len(edges), np.nan)
        spread = np.full(len(edges), np.nan)
        order = np.argsort(bi, kind="stable")
        bi_s, rel_s = bi[order], rel[order]
        bounds = np.searchsorted(bi_s, np.arange(len(edges) + 1))
        for k in range(len(edges)):
            r = rel_s[bounds[k] : bounds[k + 1]]
            if len(r) >= 3:
                q1, q2, q3 = np.percentile(r, [25, 50, 75])
                med[k] = q2
                spread[k] = (q3 - q1) / max(q2, 1e-6)
        return edges, med, spread

    # ------------------------------------------------------------------ spatial
    def heatmap(self, kind: str, smooth: bool = True) -> np.ndarray:
        res = self.res
        cols, rows = res.grid_cols, res.grid_rows
        v = self._vehicle_obs() & ~self._o("static")
        gx = np.clip((self._o("gx") * cols).astype(int), 0, cols - 1)
        gy = np.clip((self._o("gy") * rows).astype(int), 0, rows - 1)
        cell = gy * cols + gx
        dur = self._o("dur")
        n = cols * rows
        dur_total = max(self.duration, 1e-6)

        def blur(a: np.ndarray) -> np.ndarray:
            return cv2.GaussianBlur(a.astype(np.float32), (3, 3), 0.8) if smooth else a

        if kind == "density":
            g = np.bincount(cell[v], weights=dur[v], minlength=n).reshape(rows, cols) / dur_total
            return blur(g)
        if kind == "stagnation":
            m = v & (self._o("state") == STATE_STATIONARY)
            g = np.bincount(cell[m], weights=dur[m], minlength=n).reshape(rows, cols) / dur_total
            return blur(g)
        m = v & (self._o("state") != STATE_STATIONARY)
        rel = self._o("rel")
        w = np.bincount(cell[m], weights=dur[m], minlength=n).reshape(rows, cols)
        cnt = np.bincount(cell[m], minlength=n).reshape(rows, cols)
        if kind == "movement":
            s = np.bincount(cell[m], weights=rel[m] * dur[m], minlength=n).reshape(rows, cols)
            if smooth:
                s, w_b = blur(s), blur(w)
            else:
                w_b = w
            out = np.where(w_b > 1e-6, s / np.maximum(w_b, 1e-9), np.nan)
            out[blur(cnt.astype(np.float32)) < 1.5] = np.nan
            return out
        if kind == "variability":
            s1 = np.bincount(cell[m], weights=rel[m], minlength=n).reshape(rows, cols)
            s2 = np.bincount(cell[m], weights=rel[m] ** 2, minlength=n).reshape(rows, cols)
            if smooth:
                s1, s2, c2 = blur(s1), blur(s2), blur(cnt.astype(np.float32))
            else:
                c2 = cnt.astype(np.float32)
            mean = s1 / np.maximum(c2, 1e-9)
            var = np.maximum(s2 / np.maximum(c2, 1e-9) - mean ** 2, 0)
            cv = np.sqrt(var) / np.maximum(mean, 1e-6)
            cv[c2 < 4] = np.nan
            return cv
        raise ValueError(kind)

    # ------------------------------------------------------------------ key figures
    def kpis(self) -> dict:
        v = self._vehicle_obs() & ~self._o("static")
        st = self._o("state")
        dur = self._o("dur")
        moving_mask = v & (st != STATE_STATIONARY)
        rel = self._o("rel")[moving_mask]
        total_dur = float(dur[v].sum())
        stationary_share = float(dur[v & (st == STATE_STATIONARY)].sum() / total_dur) if total_dur > 0 else 0.0
        low_share = float(dur[v & (st == STATE_LOW)].sum() / total_dur) if total_dur > 0 else 0.0
        _, present, _, queue = self.presence_series()
        if len(rel) >= 5:
            q1, q2, q3 = np.percentile(rel, [25, 50, 75])
            spread = float((q3 - q1) / max(q2, 1e-6))
            median_rel = float(q2)
        else:
            spread, median_rel = float("nan"), float("nan")
        movements = int(len(self.movement_rows))
        minutes = max(self.duration / 60.0, 1e-6)
        return {
            "movements": movements,
            "movements_per_min": movements / minutes,
            "pedestrians": self.pedestrian_count(),
            "static_vehicles": self.static_count(),
            "avg_present": total_dur / max(self.duration, 1e-6),
            "max_present": float(present.max()) if len(present) else 0.0,
            "median_rel": median_rel,
            "spread": spread,
            "uniformity_label": uniformity_label(spread),
            "stationary_share": stationary_share,
            "low_share": low_share,
            "queue_seconds": float((queue > 0.5).sum() * self.res.bin_s) if len(queue) else 0.0,
            "n_directions": len([d for d in self.counts_by_direction() if d >= 0]),
            "events": len(self.events()),
        }

    def events(self) -> list[dict]:
        out = []
        for e in self.res.events:
            if e["t_end"] < self.t0 or e["t_start"] > self.t1:
                continue
            if self.region is not None and not self.region.contains(np.array(e.get("x", -1)), np.array(e.get("y", -1))):
                continue
            out.append(e)
        return out

    def stationary_episodes(self, min_s: float = 2.0) -> list[dict]:
        """Continuous stationary periods per track within the selection."""
        o = self.res.obs
        idx = self.idx[(o["state"][self.idx] == STATE_STATIONARY) & ~o["static"][self.idx]]
        if len(idx) == 0:
            return []
        order = np.lexsort((o["t"][idx], o["track"][idx]))
        idx = idx[order]
        tr, t = o["track"][idx], o["t"][idx]
        out = []
        start = 0
        for i in range(1, len(idx) + 1):
            if i == len(idx) or tr[i] != tr[start] or t[i] - t[i - 1] > 1.0:
                d = t[i - 1] - t[start] + float(o["dur"][idx[i - 1]])
                if d >= min_s:
                    out.append({
                        "track": int(tr[start]),
                        "t_start": float(t[start]),
                        "t_end": float(t[i - 1]),
                        "duration": float(d),
                        "x": float(o["gx"][idx[start]]),
                        "y": float(o["gy"][idx[start]]),
                        "cat": int(o["cat"][idx[start]]),
                        "queue": bool(o["queue"][idx[start:i]].any()),
                    })
                start = i
        return out


def uniformity_label(spread: float) -> str:
    if spread != spread:  # NaN
        return "onvoldoende gegevens"
    if spread < 0.5:
        return "gelijkmatige verkeersstroom"
    if spread < 1.0:
        return "wisselende beweging"
    return "sterk wisselende beweging"


# ---------------------------------------------------------------------- events
def detect_events(res: AnalysisResults) -> list[dict]:
    """Derive timeline events for the whole recording."""
    sel = res.select()
    bin_s = max(1.0, min(res.bin_s, 5.0)) if res.duration > 20 else 1.0
    edges, present, stationary, queue = sel.presence_series(bin_s)
    _, med, spread = sel.movement_series(bin_s)
    events: list[dict] = []
    o = res.obs

    def runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
        out, start = [], None
        for i, m in enumerate(np.append(mask, False)):
            if m and start is None:
                start = i
            elif not m and start is not None:
                if i - start >= min_len:
                    out.append((start, i - 1))
                start = None
        return out

    def location(t_a: float, t_b: float, extra: np.ndarray | None = None) -> tuple[float, float]:
        m = (o["t"] >= t_a) & (o["t"] <= t_b) & ~o["static"]
        if extra is not None:
            m &= extra
        if not m.any():
            return -1.0, -1.0
        return float(np.median(o["gx"][m])), float(np.median(o["gy"][m]))

    def add(kind: str, a: int, b: int, severity: float, text: str, extra=None) -> None:
        t_a, t_b = float(edges[a]), float(min(res.duration, edges[b] + bin_s))
        x, y = location(t_a, t_b, extra)
        events.append({"type": kind, "label": EVENT_TYPES[kind][0], "t_start": t_a, "t_end": t_b,
                       "severity": float(severity), "description": text, "x": x, "y": y})

    min_bins = max(1, int(round(2.0 / bin_s)))
    if len(present) >= 4 and present.max() > 0:
        thr = max(present.mean() + 1.5 * present.std(), 2.0)
        for a, b in runs(present >= thr, 1):
            pk = present[a : b + 1].max()
            add("peak", a, b, pk / max(present.mean(), 1e-6),
                f"Tot gemiddeld {fmt_number(pk, 1)} voertuigen tegelijk in beeld "
                f"(gemiddelde over de opname: {fmt_number(present.mean(), 1)}).")
    share = np.where(present >= 2, stationary / np.maximum(present, 1e-6), 0)
    for a, b in runs(share >= 0.6, min_bins):
        add("stagnation", a, b, float(share[a : b + 1].mean()),
            f"Het merendeel van de voertuigen staat stil ({share[a:b+1].mean()*100:.0f}% van de aanwezige voertuigen).",
            o["state"] == STATE_STATIONARY)
    for a, b in runs(queue >= 2.5, min_bins):
        add("queue", a, b, float(queue[a : b + 1].max()),
            f"Groep van gemiddeld {fmt_number(queue[a:b+1].mean(), 1)} dicht opeenstaande, (bijna) stilstaande voertuigen.",
            o["queue"])
    valid = ~np.isnan(spread)
    if valid.sum() >= 6:
        sv = spread[valid]
        thr = max(np.nanmean(sv) + 2 * np.nanstd(sv), 1.0)
        for a, b in runs(np.nan_to_num(spread, nan=0) >= thr, 1):
            add("variability", a, b, float(np.nanmax(spread[a : b + 1])),
                "De beweging van voertuigen verschilt in deze periode sterk van elkaar (mogelijke lokale verstoring).")
    for ep in sel.stationary_episodes(min_s=10.0):
        from traffic_snapshot.analysis.categories import label as cat_label

        events.append({"type": "long_stop", "label": EVENT_TYPES["long_stop"][0], "t_start": ep["t_start"],
                       "t_end": ep["t_end"], "severity": ep["duration"],
                       "description": f"{cat_label(ep['cat'])} staat circa {ep['duration']:.0f} s stil.",
                       "x": ep["x"], "y": ep["y"], "track": ep["track"]})

    events.sort(key=lambda e: e["t_start"])
    if len(events) > 200:  # keep the timeline readable: most significant per type
        events = sorted(events, key=lambda e: -e["severity"])[:200]
        events.sort(key=lambda e: e["t_start"])
    for i, e in enumerate(events):
        e["id"] = i
    return events
