"""Object detection.

Two interchangeable detectors implement :class:`Detector`:

* :class:`YoloxDetector` — YOLOX (Apache-2.0) COCO models run through ONNX
  Runtime. Uses the GPU when an accelerated execution provider is available
  (DirectML on Windows, CUDA elsewhere) and falls back to the CPU. Supports
  tiling for scenes where vehicles are very small in the image.
* :class:`MotionDetector` — background-subtraction fallback for when no model
  is available or vehicles are too small for the neural detector. It cannot
  tell vehicle types apart, so it reports ``Category.UNKNOWN`` honestly.
* :class:`HybridDetector` — the default: neural detections (with type), plus
  moving objects found by background subtraction that the neural model
  missed (typical for top-down/drone footage or motion blur). Those extra
  objects are reported as ``Category.UNKNOWN``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from traffic_snapshot.analysis.categories import COCO_TO_CATEGORY, Category
from traffic_snapshot.core.paths import models_dir

log = logging.getLogger(__name__)


@dataclass
class Detections:
    boxes: np.ndarray  # (N, 4) float32 xyxy in frame pixels
    scores: np.ndarray  # (N,) float32
    cats: np.ndarray  # (N,) int16 Category values

    @staticmethod
    def empty() -> Detections:
        return Detections(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), np.zeros(0, np.int16))

    def __len__(self) -> int:
        return len(self.scores)

    def subset(self, mask: np.ndarray) -> Detections:
        return Detections(self.boxes[mask], self.scores[mask], self.cats[mask])


class Detector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> Detections: ...

    def describe(self) -> dict: ...


# --------------------------------------------------------------------------- utils
def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between (N,4) and (M,4) xyxy boxes."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), np.float32)
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return (inter / np.maximum(union, 1e-6)).astype(np.float32)


def nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> np.ndarray:
    """Greedy NMS; returns kept indices sorted by descending score."""
    order = np.argsort(-scores)
    keep: list[int] = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        if len(order) == 1:
            break
        ious = box_iou(boxes[i : i + 1], boxes[order[1:]])[0]
        order = order[1:][ious <= thr]
    return np.array(keep, dtype=np.int64)


def _suppress_riders(det: Detections) -> Detections:
    """A person on a bicycle/motorcycle is one road user: keep the vehicle only."""
    persons = np.where(det.cats == Category.PEDESTRIAN)[0]
    rides = np.where((det.cats == Category.BICYCLE) | (det.cats == Category.MOTORCYCLE))[0]
    if len(persons) == 0 or len(rides) == 0:
        return det
    keep = np.ones(len(det), bool)
    pb, rb = det.boxes[persons], det.boxes[rides]
    for pi, p in zip(persons, pb):
        pcx = (p[0] + p[2]) / 2
        p_area = max(1e-6, (p[2] - p[0]) * (p[3] - p[1]))
        for r in rb:
            iw = max(0.0, min(p[2], r[2]) - max(p[0], r[0]))
            ih = max(0.0, min(p[3], r[3]) - max(p[1], r[1]))
            if iw * ih / p_area > 0.25 and r[0] <= pcx <= r[2]:
                keep[pi] = False
                break
    return det.subset(keep)


def _merge_vehicle_duplicates(det: Detections, thr: float = 0.6) -> Detections:
    """Car/truck/bus labels on the same object: keep the most confident one."""
    if len(det) < 2:
        return det
    motor = np.isin(det.cats, [Category.CAR, Category.TRUCK, Category.BUS])
    idx = np.where(motor)[0]
    if len(idx) < 2:
        return det
    kept = idx[nms(det.boxes[idx], det.scores[idx], thr)]
    mask = ~motor
    mask[kept] = True
    return det.subset(mask)


# --------------------------------------------------------------------------- YOLOX
@dataclass(frozen=True)
class ModelSpec:
    key: str
    file: str
    input_size: int
    sha256: str
    url: str
    description: str


MODEL_SPECS: dict[str, ModelSpec] = {
    "yolox_s": ModelSpec(
        "yolox_s",
        "yolox_s.onnx",
        640,
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
        "YOLOX-S (COCO, Apache-2.0), invoer 640×640",
    ),
    "yolox_tiny": ModelSpec(
        "yolox_tiny",
        "yolox_tiny.onnx",
        416,
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        "YOLOX-Tiny (COCO, Apache-2.0), invoer 416×416",
    ),
}


def available_models() -> list[str]:
    return [k for k, s in MODEL_SPECS.items() if (models_dir() / s.file).is_file()]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


PREFERRED_PROVIDERS = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]


def create_session(model_path: Path, allow_gpu: bool = True):
    import onnxruntime as ort

    available = ort.get_available_providers()
    providers = [p for p in PREFERRED_PROVIDERS if p in available and (allow_gpu or p == "CPUExecutionProvider")]
    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")

    opts = ort.SessionOptions()
    # Leave one core for the user interface.
    opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    last_exc: Exception | None = None
    for i in range(len(providers)):
        try_providers = providers[i:]
        try:
            sess = ort.InferenceSession(str(model_path), sess_options=opts, providers=try_providers)
            return sess, sess.get_providers()[0]
        except Exception as exc:  # GPU provider failed to initialise -> next one
            log.warning("ONNX session with %s failed: %s", try_providers[0], exc)
            last_exc = exc
    raise RuntimeError(f"Kan detectiemodel niet laden: {last_exc}")


class YoloxDetector:
    def __init__(
        self,
        model_key: str = "yolox_s",
        *,
        conf_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        tiles: tuple[int, int] = (1, 1),
        allow_gpu: bool = True,
        keep_pedestrians: bool = True,
    ) -> None:
        spec = MODEL_SPECS[model_key]
        self.spec = spec
        self.model_path = models_dir() / spec.file
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        self.session, self.provider = create_session(self.model_path, allow_gpu)
        self.input_name = self.session.get_inputs()[0].name
        self.size = spec.input_size
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.tiles = tiles
        self.keep_pedestrians = keep_pedestrians
        self.name = spec.key
        self._grids, self._strides = self._make_grids(self.size)
        self._class_ids = np.array(sorted(COCO_TO_CATEGORY), dtype=np.int64)

    @staticmethod
    def _make_grids(size: int) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for s in (8, 16, 32):
            n = size // s
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            g = np.stack((xv, yv), 2).reshape(-1, 2)
            grids.append(g)
            strides.append(np.full((g.shape[0], 1), s))
        return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)

    @property
    def device_label(self) -> str:
        return {
            "CUDAExecutionProvider": "GPU (CUDA)",
            "DmlExecutionProvider": "GPU (DirectML)",
        }.get(self.provider, "CPU")

    def describe(self) -> dict:
        return {
            "type": "neural",
            "model": self.spec.key,
            "model_file": self.spec.file,
            "model_sha256": self.spec.sha256,
            "description": self.spec.description,
            "input_size": self.size,
            "tiles": list(self.tiles),
            "conf_threshold": self.conf_threshold,
            "nms_threshold": self.nms_threshold,
            "execution_provider": self.provider,
        }

    def _infer(self, img: np.ndarray) -> Detections:
        h, w = img.shape[:2]
        r = min(self.size / h, self.size / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        padded = np.full((self.size, self.size, 3), 114, np.uint8)
        padded[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
        out = self.session.run(None, {self.input_name: blob})[0][0]

        xy = (out[:, :2] + self._grids) * self._strides
        wh = np.exp(np.clip(out[:, 2:4], -10, 10)) * self._strides
        obj = out[:, 4]
        cls_scores = out[:, 5 + self._class_ids] * obj[:, None]
        best = cls_scores.argmax(1)
        scores = cls_scores[np.arange(len(best)), best]
        keep = scores >= self.conf_threshold
        if not keep.any():
            return Detections.empty()
        xy, wh, scores, coco = xy[keep], wh[keep], scores[keep], self._class_ids[best[keep]]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], 1) / r
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w - 1)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h - 1)
        cats = np.array([COCO_TO_CATEGORY[int(c)] for c in coco], np.int16)
        # class-aware NMS (offset trick)
        offs = cats[:, None].astype(np.float32) * 10000.0
        kept = nms(boxes + offs, scores, self.nms_threshold)
        return Detections(boxes[kept].astype(np.float32), scores[kept].astype(np.float32), cats[kept])

    def _tile_rects(self, w: int, h: int) -> list[tuple[int, int, int, int]]:
        cols, rows = self.tiles
        if cols * rows <= 1:
            return []
        ov = 0.15
        tw, th = int(w / (cols - (cols - 1) * ov)), int(h / (rows - (rows - 1) * ov))
        rects = []
        for r in range(rows):
            for c in range(cols):
                x0 = int(round(c * tw * (1 - ov))) if cols > 1 else 0
                y0 = int(round(r * th * (1 - ov))) if rows > 1 else 0
                rects.append((x0, y0, min(w, x0 + tw), min(h, y0 + th)))
        return rects

    def detect(self, frame: np.ndarray) -> Detections:
        h, w = frame.shape[:2]
        parts = [self._infer(frame)]
        for x0, y0, x1, y1 in self._tile_rects(w, h):
            d = self._infer(frame[y0:y1, x0:x1])
            if len(d):
                d.boxes[:, [0, 2]] += x0
                d.boxes[:, [1, 3]] += y0
                parts.append(d)
        if len(parts) > 1:
            det = Detections(
                np.concatenate([p.boxes for p in parts]),
                np.concatenate([p.scores for p in parts]),
                np.concatenate([p.cats for p in parts]),
            )
            offs = det.cats[:, None].astype(np.float32) * 10000.0
            det = det.subset(nms(det.boxes + offs, det.scores, self.nms_threshold))
        else:
            det = parts[0]
        det = _merge_vehicle_duplicates(det)
        det = _suppress_riders(det)
        if not self.keep_pedestrians:
            det = det.subset(det.cats != Category.PEDESTRIAN)
        return det


# --------------------------------------------------------------------------- motion
@dataclass
class MotionDetector:
    """Background subtraction for static cameras; category is always UNKNOWN."""

    expected_vehicle_px: float = 40.0
    history_frames: int = 600  # ~ a minute of analysed frames
    name: str = "motion"
    _bg: cv2.BackgroundSubtractor | None = field(default=None, repr=False)
    device_label: str = "CPU"

    def __post_init__(self) -> None:
        # A long history and a low background ratio keep waiting (stationary) vehicles in
        # the foreground for roughly 0.35 × history frames instead of a few seconds.
        self._bg = cv2.createBackgroundSubtractorMOG2(history=self.history_frames, varThreshold=32,
                                                      detectShadows=True)
        self._bg.setBackgroundRatio(0.7)
        self._scale = 1.0

    def describe(self) -> dict:
        return {
            "type": "motion",
            "model": "motion",
            "description": "Bewegingsdetectie (achtergrondsubtractie, MOG2) — geen typeherkenning",
            "expected_vehicle_px": round(self.expected_vehicle_px, 1),
            "history_frames": self.history_frames,
        }

    def detect(self, frame: np.ndarray) -> Detections:
        h, w = frame.shape[:2]
        # Work at a reduced resolution when vehicles are large (faster, less noise).
        scale = min(1.0, max(0.25, 48.0 / max(self.expected_vehicle_px, 1.0)))
        small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1 else frame
        mask = self._bg.apply(small)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)  # drop shadows (127)
        veh = self.expected_vehicle_px * scale
        # small kernel: closes holes inside a vehicle without bridging the gap to the next one
        k = max(3, int(veh * 0.07) | 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = (0.3 * veh) ** 2
        boxes = []
        for c in contours:
            if cv2.contourArea(c) < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            boxes.append((x / scale, y / scale, (x + bw) / scale, (y + bh) / scale))
        if not boxes:
            return Detections.empty()
        b = np.array(boxes, np.float32)
        return Detections(b, np.full(len(b), 0.5, np.float32), np.full(len(b), Category.UNKNOWN, np.int16))


# --------------------------------------------------------------------------- hybrid
class HybridDetector:
    """Neural detector supplemented by motion detection for missed movers."""

    MOTION_SCORE = 0.42  # enough to start a track, below typical confident neural scores

    def __init__(self, neural: YoloxDetector, motion: MotionDetector) -> None:
        self.neural = neural
        self.motion = motion
        self.name = f"{neural.name}+motion"
        self.device_label = neural.device_label

    def describe(self) -> dict:
        d = self.neural.describe()
        d["type"] = "hybrid"
        d["supplement"] = self.motion.describe()
        return d

    def detect(self, frame: np.ndarray) -> Detections:
        det = self.neural.detect(frame)
        mot = self.motion.detect(frame)
        if len(mot) == 0:
            return det
        h, w = frame.shape[:2]
        area = (mot.boxes[:, 2] - mot.boxes[:, 0]) * (mot.boxes[:, 3] - mot.boxes[:, 1])
        keep = area < 0.25 * w * h
        if len(det):
            iou = box_iou(mot.boxes, det.boxes)
            # also suppress motion blobs mostly covered by a neural box
            ix1 = np.maximum(mot.boxes[:, None, 0], det.boxes[None, :, 0])
            iy1 = np.maximum(mot.boxes[:, None, 1], det.boxes[None, :, 1])
            ix2 = np.minimum(mot.boxes[:, None, 2], det.boxes[None, :, 2])
            iy2 = np.minimum(mot.boxes[:, None, 3], det.boxes[None, :, 3])
            inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
            cover = inter / np.maximum(area[:, None], 1e-6)
            keep &= (iou.max(1) < 0.3) & (cover.max(1) < 0.5)
        if not keep.any():
            return det
        extra = mot.subset(keep)
        extra.scores[:] = self.MOTION_SCORE
        return Detections(
            np.concatenate([det.boxes, extra.boxes]),
            np.concatenate([det.scores, extra.scores]),
            np.concatenate([det.cats, extra.cats]),
        )
