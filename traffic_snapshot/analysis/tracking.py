"""Multi-object tracking.

A compact ByteTrack-style tracker:

* each track has a constant-velocity motion model (centre + size, with
  velocity in pixels per *second*, so a varying frame stride is handled
  naturally);
* association happens in two passes — confident detections first, then
  low-confidence ones to keep tracks alive through partial occlusion;
* matching combines IoU with a size-normalised centre distance, so small or
  fast objects that no longer overlap their prediction can still be matched;
* unmatched tracks survive for ``max_age_s`` seconds before being closed.

The tracker also reports per-step stability statistics that the adaptive
frame-sampling controller uses to decide whether the analysis must become
more precise.

The whole state is plain Python/numpy and picklable, which is what makes
checkpointing and resuming an interrupted analysis possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from traffic_snapshot.analysis.detection import Detections, box_iou


@dataclass
class Track:
    id: int
    cx: float
    cy: float
    w: float
    h: float
    vx: float = 0.0
    vy: float = 0.0
    last_t: float = 0.0
    hits: int = 1
    missed_s: float = 0.0
    cat_votes: dict[int, float] = field(default_factory=dict)

    def predicted(self, t: float) -> tuple[float, float]:
        dt = t - self.last_t
        return self.cx + self.vx * dt, self.cy + self.vy * dt

    def box_at(self, t: float) -> np.ndarray:
        px, py = self.predicted(t)
        return np.array([px - self.w / 2, py - self.h / 2, px + self.w / 2, py + self.h / 2], np.float32)

    @property
    def scale(self) -> float:
        return float(np.sqrt(max(self.w * self.h, 1.0)))

    @property
    def category(self) -> int:
        return max(self.cat_votes, key=self.cat_votes.get) if self.cat_votes else 6


@dataclass
class StepStats:
    """Stability signals for one processed frame."""

    matched: int = 0
    step_ratios: list[float] = field(default_factory=list)  # displacement / object size
    births_interior: int = 0  # new tracks that did not enter from the image border
    births: int = 0
    active: int = 0


@dataclass
class TrackerConfig:
    high_conf: float = 0.4
    low_conf: float = 0.25
    min_match_score: float = 0.15
    max_age_s: float = 1.5
    gate: float = 1.2  # centre distance gate in units of object size
    velocity_smoothing: float = 0.5
    border_margin: float = 0.06  # fraction of image size


class Tracker:
    name = "bytetrack-lite"

    def __init__(self, frame_w: int, frame_h: int, config: TrackerConfig | None = None) -> None:
        self.cfg = config or TrackerConfig()
        self.frame_w = frame_w
        self.frame_h = frame_h
        self.tracks: list[Track] = []
        self.next_id = 1

    def describe(self) -> dict:
        c = self.cfg
        return {
            "name": self.name,
            "motion_model": "constant velocity (px/s)",
            "association": "two-pass IoU + centre distance, greedy",
            "high_conf": c.high_conf,
            "low_conf": c.low_conf,
            "max_age_s": c.max_age_s,
            "gate": c.gate,
        }

    # ------------------------------------------------------------------ core
    def _near_border(self, box: np.ndarray) -> bool:
        mx, my = self.frame_w * self.cfg.border_margin, self.frame_h * self.cfg.border_margin
        return box[0] < mx or box[1] < my or box[2] > self.frame_w - mx or box[3] > self.frame_h - my

    def _score_matrix(self, tracks: list[Track], boxes: np.ndarray, t: float) -> np.ndarray:
        if not tracks or len(boxes) == 0:
            return np.zeros((len(tracks), len(boxes)), np.float32)
        pred = np.stack([tr.box_at(t) for tr in tracks])
        iou = box_iou(pred, boxes)
        pc = (pred[:, :2] + pred[:, 2:]) / 2
        dc = (boxes[:, :2] + boxes[:, 2:]) / 2
        dist = np.linalg.norm(pc[:, None, :] - dc[None, :, :], axis=2)
        scale = np.array([tr.scale for tr in tracks])[:, None]
        # objects may be missed for a while: widen the gate with elapsed time
        missed = np.array([tr.missed_s for tr in tracks])[:, None]
        gate = self.cfg.gate * scale * (1.0 + missed)
        prox = np.clip(1.0 - dist / gate, 0.0, 1.0)
        # size consistency: penalise large size jumps
        tw = np.array([tr.w * tr.h for tr in tracks])[:, None]
        dw = ((boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]))[None, :]
        size_ratio = np.minimum(tw, dw) / np.maximum(np.maximum(tw, dw), 1e-6)
        score = np.maximum(iou, 0.6 * prox) * np.where(size_ratio > 0.25, 1.0, 0.3)
        return score.astype(np.float32)

    @staticmethod
    def _greedy(score: np.ndarray, min_score: float) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        if score.size == 0:
            return pairs
        s = score.copy()
        while True:
            i, j = np.unravel_index(np.argmax(s), s.shape)
            if s[i, j] < min_score:
                break
            pairs.append((int(i), int(j)))
            s[i, :] = -1
            s[:, j] = -1
        return pairs

    def _update_track(self, tr: Track, box: np.ndarray, score: float, cat: int, t: float, stats: StepStats) -> None:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        dt = t - tr.last_t
        if dt > 0:
            step = float(np.hypot(cx - tr.cx, cy - tr.cy))
            stats.step_ratios.append(step / tr.scale)
            a = self.cfg.velocity_smoothing
            nvx, nvy = (cx - tr.cx) / dt, (cy - tr.cy) / dt
            if tr.hits == 1:
                tr.vx, tr.vy = nvx, nvy
            else:
                tr.vx, tr.vy = a * nvx + (1 - a) * tr.vx, a * nvy + (1 - a) * tr.vy
        tr.cx, tr.cy = float(cx), float(cy)
        tr.w = float(0.6 * (box[2] - box[0]) + 0.4 * tr.w)
        tr.h = float(0.6 * (box[3] - box[1]) + 0.4 * tr.h)
        tr.last_t = t
        tr.hits += 1
        tr.missed_s = 0.0
        tr.cat_votes[cat] = tr.cat_votes.get(cat, 0.0) + score
        stats.matched += 1

    def update(self, det: Detections, t: float) -> tuple[list[tuple[int, np.ndarray, int, float]], StepStats]:
        """Associate detections at time ``t`` (seconds).

        Returns the list of ``(track_id, box, category, score)`` for every
        track observed in this frame, plus stability statistics.
        """
        stats = StepStats()
        observed: list[tuple[int, np.ndarray, int, float]] = []
        high = det.scores >= self.cfg.high_conf
        low = (det.scores >= self.cfg.low_conf) & ~high

        unmatched_tracks = list(range(len(self.tracks)))
        # pass 1: confident detections
        hi_idx = np.where(high)[0]
        score = self._score_matrix(self.tracks, det.boxes[hi_idx], t)
        used_hi: set[int] = set()
        for ti, dj in self._greedy(score, self.cfg.min_match_score):
            d = hi_idx[dj]
            tr = self.tracks[ti]
            self._update_track(tr, det.boxes[d], float(det.scores[d]), int(det.cats[d]), t, stats)
            observed.append((tr.id, det.boxes[d].copy(), int(det.cats[d]), float(det.scores[d])))
            used_hi.add(dj)
            unmatched_tracks.remove(ti)

        # pass 2: low-confidence detections for remaining tracks
        lo_idx = np.where(low)[0]
        rem = [self.tracks[i] for i in unmatched_tracks]
        score = self._score_matrix(rem, det.boxes[lo_idx], t)
        matched_rem: set[int] = set()
        for ri, dj in self._greedy(score, max(self.cfg.min_match_score, 0.25)):
            d = lo_idx[dj]
            tr = rem[ri]
            self._update_track(tr, det.boxes[d], float(det.scores[d]), int(det.cats[d]), t, stats)
            observed.append((tr.id, det.boxes[d].copy(), int(det.cats[d]), float(det.scores[d])))
            matched_rem.add(ri)

        # age unmatched tracks
        survivors: list[Track] = []
        rem_ids = {id(rem[i]) for i in range(len(rem)) if i not in matched_rem}
        for tr in self.tracks:
            if id(tr) in rem_ids:
                tr.missed_s = t - tr.last_t
                if tr.missed_s > self.cfg.max_age_s:
                    continue
            survivors.append(tr)
        self.tracks = survivors

        # births from unmatched confident detections
        for dj, d in enumerate(hi_idx):
            if dj in used_hi:
                continue
            b = det.boxes[d]
            tr = Track(
                id=self.next_id,
                cx=float((b[0] + b[2]) / 2),
                cy=float((b[1] + b[3]) / 2),
                w=float(b[2] - b[0]),
                h=float(b[3] - b[1]),
                last_t=t,
                cat_votes={int(det.cats[d]): float(det.scores[d])},
            )
            self.next_id += 1
            self.tracks.append(tr)
            observed.append((tr.id, b.copy(), int(det.cats[d]), float(det.scores[d])))
            stats.births += 1
            if not self._near_border(b):
                stats.births_interior += 1

        stats.active = len(self.tracks)
        return observed, stats
