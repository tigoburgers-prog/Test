"""Turns raw tracked observations into analysable, *relative* movement data.

Key idea — relative movement
    Pixel speeds depend on perspective: a car far away moves fewer pixels per
    second than the same car nearby. Dividing the displacement by the
    object's own apparent size (``sqrt(w*h)``) removes most of that effect.
    The result, "object sizes per second", is then divided by the median of
    all moving observations in *this* video. So a relative movement of 1.0
    means "typical for this recording", 0.5 half as fast, 2.0 twice as fast.
    No conversion to km/h is made anywhere.

Outputs per observation: relative movement, movement state (moving / low /
stationary), queue indication, represented duration. Outputs per track:
category (confidence-weighted vote), direction cluster, flags for traffic
movements vs. objects that never moved (e.g. parked vehicles).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from traffic_snapshot.analysis.categories import Category

log = logging.getLogger(__name__)

STATE_MOVING, STATE_LOW, STATE_STATIONARY = 0, 1, 2

SPEED_WINDOW_S = 0.5  # half-window for displacement estimation
STATIONARY_THR = 0.12  # object sizes per second
LOW_REL_THR = 0.4  # relative movement below this = "lage beweging"
MIN_TRACK_OBS = 3
MIN_TRACK_DURATION_S = 0.3
QUEUE_MIN_SIZE = 3
QUEUE_LINK = 2.2  # max gap between queued vehicles, in object sizes


@dataclass
class DirectionCluster:
    id: int
    label: str
    angle_deg: float  # image coordinates: 0 = right, 90 = down
    description: str
    arrow: str
    n_tracks: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def direction_text(angle_deg: float) -> tuple[str, str]:
    """Neutral, image-relative description. Never geographic."""
    names = [
        ("→", "van links naar rechts in beeld"),
        ("↘", "naar rechtsonder in beeld"),
        ("↓", "van boven naar onder in beeld"),
        ("↙", "naar linksonder in beeld"),
        ("←", "van rechts naar links in beeld"),
        ("↖", "naar linksboven in beeld"),
        ("↑", "van onder naar boven in beeld"),
        ("↗", "naar rechtsboven in beeld"),
    ]
    idx = int(((angle_deg % 360) + 22.5) // 45) % 8
    return names[idx]


def _angle_diff(a: np.ndarray | float, b: float) -> np.ndarray:
    return np.abs((np.asarray(a) - b + 180) % 360 - 180)


def cluster_directions(angles: np.ndarray, min_share: float = 0.08) -> list[float]:
    """Find dominant movement directions via a smoothed circular histogram."""
    if len(angles) == 0:
        return []
    bins = 36
    hist, _ = np.histogram(angles % 360, bins=bins, range=(0, 360))
    kernel = np.array([1, 2, 3, 2, 1], float)
    padded = np.concatenate([hist[-2:], hist, hist[:2]]).astype(float)
    smooth = np.convolve(padded, kernel / kernel.sum(), mode="valid")
    peaks = []
    for i in range(bins):
        if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[(i + 1) % bins] and smooth[i] > 0:
            peaks.append(i)
    min_count = max(1.0, min_share * len(angles))
    centers: list[tuple[float, float]] = []
    for i in sorted(peaks, key=lambda i: -smooth[i]):
        center = (i + 0.5) * (360 / bins)
        near = angles[_angle_diff(angles, center) < 30]
        if len(near) < min_count:
            continue
        # refine with the circular mean of nearby angles
        rad = np.deg2rad(near)
        center = float(np.rad2deg(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360)
        if all(_angle_diff(center, c) > 50 for c, _ in centers):
            centers.append((center, float(len(near))))
    centers.sort(key=lambda c: -c[1])
    return [c for c, _ in centers[:6]]


def _track_slices(track_ids: np.ndarray) -> list[tuple[int, int, int]]:
    """(track_id, start, end) for an array sorted by track."""
    if len(track_ids) == 0:
        return []
    change = np.flatnonzero(np.diff(track_ids)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(track_ids)]])
    return [(int(track_ids[s]), int(s), int(e)) for s, e in zip(starts, ends)]


def postprocess(
    raw: dict[str, np.ndarray],
    frame_idx: np.ndarray,
    frame_t: np.ndarray,
    fps: float,
    width: int,
    height: int,
    duration_s: float,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """Returns (observations, tracks, info)."""
    # Duration represented by each processed frame (handles variable stride).
    if len(frame_t):
        dur_per_frame = np.diff(np.concatenate([frame_t, [min(duration_s, frame_t[-1] + 1.0 / fps)]]))
        dur_per_frame = np.clip(dur_per_frame, 1.0 / fps, 1.0)
    else:
        dur_per_frame = np.zeros(0)

    order = np.lexsort((raw["t"], raw["track"]))
    frame = raw["frame"][order]
    t = raw["t"][order]
    track = raw["track"][order]
    box = raw["box"][order]
    cat_raw = raw["cat"][order]
    score = raw["score"][order]

    keep = np.zeros(len(t), bool)
    for _tid, s, e in _track_slices(track):
        if e - s >= MIN_TRACK_OBS and t[e - 1] - t[s] >= MIN_TRACK_DURATION_S:
            keep[s:e] = True
    frame, t, track, box, cat_raw, score = (a[keep] for a in (frame, t, track, box, cat_raw, score))
    n = len(t)

    w = np.maximum(box[:, 2] - box[:, 0], 1.0)
    h = np.maximum(box[:, 3] - box[:, 1], 1.0)
    cx = (box[:, 0] + box[:, 2]) / 2
    cy = (box[:, 1] + box[:, 3]) / 2
    gx, gy = cx, box[:, 3]  # ground contact point
    scale = np.sqrt(w * h)
    speed_norm = np.zeros(n)
    fidx = np.searchsorted(frame_idx, frame).clip(0, max(0, len(frame_idx) - 1))
    dur = dur_per_frame[fidx] if len(dur_per_frame) else np.zeros(n)

    slices = _track_slices(track)
    tr_ids, tr_cat, tr_t0, tr_t1, tr_n = [], [], [], [], []
    tr_net, tr_angle, tr_scale, tr_statfrac, tr_moving, tr_static = [], [], [], [], [], []
    tr_x0, tr_y0, tr_x1, tr_y1 = [], [], [], []
    final_cat = np.zeros(n, np.int16)
    for tid, s, e in slices:
        tt = t[s:e]
        lo = np.searchsorted(tt, tt - SPEED_WINDOW_S, "left")
        hi = np.searchsorted(tt, tt + SPEED_WINDOW_S, "right") - 1
        span = tt[hi] - tt[lo]
        ok = span > 1e-6
        d = np.hypot(cx[s:e][hi] - cx[s:e][lo], cy[s:e][hi] - cy[s:e][lo])
        sc = (scale[s:e][hi] + scale[s:e][lo]) / 2
        sp = np.zeros(e - s)
        sp[ok] = d[ok] / span[ok] / sc[ok]
        speed_norm[s:e] = sp

        votes: dict[int, float] = {}
        for c, sco in zip(cat_raw[s:e], score[s:e]):
            # A type recognised by the neural detector always outweighs "unknown".
            wgt = 0.02 if c == Category.UNKNOWN else 1.0
            votes[int(c)] = votes.get(int(c), 0.0) + float(sco) * wgt
        c = max(votes, key=votes.get)
        final_cat[s:e] = c

        k = max(1, (e - s) // 5)
        p0 = np.array([gx[s : s + k].mean(), gy[s : s + k].mean()])
        p1 = np.array([gx[e - k : e].mean(), gy[e - k : e].mean()])
        vec = p1 - p0
        med_scale = float(np.median(scale[s:e]))
        net = float(np.hypot(*vec)) / med_scale
        stat_frac = float(np.mean(sp < STATIONARY_THR))
        moving = net >= 1.0 or float(np.mean(sp >= STATIONARY_THR)) >= 0.3
        static = (not moving) and stat_frac >= 0.8

        tr_ids.append(tid)
        tr_cat.append(c)
        tr_t0.append(tt[0])
        tr_t1.append(tt[-1])
        tr_n.append(e - s)
        tr_net.append(net)
        tr_angle.append(float(np.degrees(np.arctan2(vec[1], vec[0])) % 360))
        tr_scale.append(med_scale)
        tr_statfrac.append(stat_frac)
        tr_moving.append(moving)
        tr_static.append(static)
        tr_x0.append(p0[0] / width)
        tr_y0.append(p0[1] / height)
        tr_x1.append(p1[0] / width)
        tr_y1.append(p1[1] / height)

    tracks = {
        "id": np.array(tr_ids, np.int64),
        "cat": np.array(tr_cat, np.int16),
        "t0": np.array(tr_t0, np.float64),
        "t1": np.array(tr_t1, np.float64),
        "n": np.array(tr_n, np.int32),
        "net": np.array(tr_net, np.float32),
        "angle": np.array(tr_angle, np.float32),
        "scale": np.array(tr_scale, np.float32),
        "stat_frac": np.array(tr_statfrac, np.float32),
        "moving": np.array(tr_moving, bool),
        "static": np.array(tr_static, bool),
        "x0": np.array(tr_x0, np.float32),
        "y0": np.array(tr_y0, np.float32),
        "x1": np.array(tr_x1, np.float32),
        "y1": np.array(tr_y1, np.float32),
    }
    n_tracks = len(tr_ids)

    # map observation -> track row
    track_row = np.searchsorted(tracks["id"], track) if n_tracks else np.zeros(n, np.int64)
    static_obs = tracks["static"][track_row] if n_tracks else np.zeros(n, bool)

    # ---------------------------------------------------------------- relative movement
    stationary = speed_norm < STATIONARY_THR
    ref_mask = ~stationary & ~static_obs
    ref = float(np.median(speed_norm[ref_mask])) if ref_mask.sum() >= 5 else (
        float(np.median(speed_norm[speed_norm > 0])) if (speed_norm > 0).any() else 1.0
    )
    ref = max(ref, 1e-3)
    rel = speed_norm / ref
    state = np.full(n, STATE_MOVING, np.int8)
    state[rel < LOW_REL_THR] = STATE_LOW
    state[stationary] = STATE_STATIONARY

    # ---------------------------------------------------------------- directions
    dir_track = np.full(n_tracks, -1, np.int16)
    clusters: list[DirectionCluster] = []
    if n_tracks:
        eligible = tracks["moving"] & (tracks["net"] >= 1.5)
        centers = cluster_directions(tracks["angle"][eligible])
        for i, c in enumerate(centers):
            arrow, desc = direction_text(c)
            clusters.append(DirectionCluster(i, f"Richting {chr(65 + i)}", round(c, 1), desc, arrow, 0))
        if centers:
            diffs = np.stack([_angle_diff(tracks["angle"], c) for c in centers], 1)
            best = diffs.argmin(1)
            ok = eligible & (diffs[np.arange(n_tracks), best] <= 60)
            dir_track[ok] = best[ok]
            for cl in clusters:
                cl.n_tracks = int((dir_track == cl.id).sum())
    tracks["direction"] = dir_track

    # ---------------------------------------------------------------- queue indication
    queue = np.zeros(n, bool)
    slow = (state >= STATE_LOW) & ~static_obs
    if slow.any():
        idx = np.flatnonzero(slow)
        fr = frame[idx]
        o = np.argsort(fr, kind="stable")
        idx, fr = idx[o], fr[o]
        bounds = np.flatnonzero(np.diff(fr)) + 1
        for grp in np.split(idx, bounds):
            if len(grp) < QUEUE_MIN_SIZE:
                continue
            pts = np.stack([gx[grp], gy[grp]], 1)
            sc = scale[grp]
            dist = np.linalg.norm(pts[:, None] - pts[None], axis=2)
            link = dist <= QUEUE_LINK * (sc[:, None] + sc[None, :]) / 2
            # connected components (single linkage)
            label = -np.ones(len(grp), int)
            cur = 0
            for i in range(len(grp)):
                if label[i] >= 0:
                    continue
                stack = [i]
                label[i] = cur
                while stack:
                    j = stack.pop()
                    for k in np.flatnonzero(link[j] & (label < 0)):
                        label[k] = cur
                        stack.append(k)
                cur += 1
            for lab in range(cur):
                members = grp[label == lab]
                if len(members) >= QUEUE_MIN_SIZE:
                    queue[members] = True

    # ---------------------------------------------------------------- per-track movement stats
    if n_tracks:
        rel_sum = np.bincount(track_row, weights=np.where(stationary, 0, rel) * dur, minlength=n_tracks)
        mov_dur = np.bincount(track_row, weights=np.where(stationary, 0, dur), minlength=n_tracks)
        tracks["mean_rel"] = np.where(mov_dur > 0, rel_sum / np.maximum(mov_dur, 1e-9), 0).astype(np.float32)
        tracks["stationary_s"] = np.bincount(
            track_row, weights=np.where(stationary, dur, 0), minlength=n_tracks
        ).astype(np.float32)
    else:
        tracks["mean_rel"] = np.zeros(0, np.float32)
        tracks["stationary_s"] = np.zeros(0, np.float32)

    # sort observations by frame for fast playback lookup
    o = np.lexsort((track, frame))
    obs = {
        "frame": frame[o],
        "t": t[o],
        "track": track[o],
        "track_row": np.asarray(track_row)[o].astype(np.int32),
        "box": box[o],
        "cat": final_cat[o],
        "gx": (gx[o] / width).astype(np.float32),
        "gy": (gy[o] / height).astype(np.float32),
        "scale": scale[o].astype(np.float32),
        "speed_norm": speed_norm[o].astype(np.float32),
        "rel": rel[o].astype(np.float32),
        "state": state[o],
        "queue": queue[o],
        "static": static_obs[o],
        "dur": dur[o].astype(np.float32),
        "direction": (dir_track[track_row][o] if n_tracks else np.zeros(n, np.int16)).astype(np.int16),
    }
    info = {
        "reference_speed_norm": ref,
        "directions": [c.to_dict() for c in clusters],
        "n_raw_tracks": int(len(np.unique(raw["track"]))) if len(raw["track"]) else 0,
        "n_tracks": n_tracks,
        "thresholds": {
            "stationary_sizes_per_s": STATIONARY_THR,
            "low_relative": LOW_REL_THR,
            "queue_min_size": QUEUE_MIN_SIZE,
            "queue_link_sizes": QUEUE_LINK,
            "speed_window_s": SPEED_WINDOW_S * 2,
        },
    }
    log.info("postprocess: %d obs, %d tracks, ref=%.3f, %d directions", n, n_tracks, ref, len(clusters))
    return obs, tracks, info


def is_vehicle_cat(cats: np.ndarray) -> np.ndarray:
    return cats != Category.PEDESTRIAN
