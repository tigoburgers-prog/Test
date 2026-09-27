import numpy as np
import pytest

from traffic_snapshot.analysis.categories import Category
from traffic_snapshot.analysis.detection import Detections, box_iou, nms
from traffic_snapshot.analysis.postprocess import STATE_STATIONARY, cluster_directions, direction_text, postprocess
from traffic_snapshot.analysis.strategy import AdaptiveSampler, WindowStats, build_plan
from traffic_snapshot.analysis.tracking import Tracker
from traffic_snapshot.core.models import AnalysisSettings, VideoInfo


def video(w=1920, h=1080, fps=25.0, n=2500):
    return VideoInfo("v.mp4", n / fps, fps, n, w, h, 1)


def test_iou_and_nms():
    a = np.array([[0, 0, 10, 10], [5, 5, 15, 15]], np.float32)
    iou = box_iou(a, a)
    assert iou[0, 0] == pytest.approx(1) and iou[0, 1] == pytest.approx(25 / 175)
    keep = nms(np.array([[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 60, 60]], np.float32),
               np.array([0.9, 0.8, 0.7], np.float32), 0.5)
    assert list(keep) == [0, 2]


def test_plan_uses_speed_for_sampling_only():
    slow = build_plan(video(), AnalysisSettings(30, 100), ["yolox_s"])
    fast = build_plan(video(), AnalysisSettings(120, 100), ["yolox_s"])
    assert slow.base_stride > fast.base_stride >= 1
    assert slow.base_stride / 25 <= 0.5  # at least 2 analysed frames per second


def test_plan_scene_width_drives_detector_choice():
    near = build_plan(video(), AnalysisSettings(50, 20), ["yolox_s"])
    far = build_plan(video(), AnalysisSettings(50, 500), ["yolox_s"])
    very_far = build_plan(video(), AnalysisSettings(50, 1000), ["yolox_s"])
    assert near.tiles == (1, 1)
    assert far.tiles[0] * far.tiles[1] > 1
    assert very_far.detector in ("yolox_s", "motion")
    assert build_plan(video(), AnalysisSettings(50, 100), []).detector == "motion"


def test_adaptive_sampler_refines_and_relaxes():
    s = AdaptiveSampler(stride=4, base_stride=4, max_stride=8)
    w = WindowStats(matched=20, step_ratios=[0.9] * 20)
    assert s.decide(w) == "refine" and s.stride == 2
    calm = WindowStats(matched=20, step_ratios=[0.05] * 20)
    decisions = [s.decide(calm) for _ in range(3)]
    assert decisions[-1] == "relax" and s.stride == 4


def test_tracker_keeps_identities():
    tr = Tracker(1000, 500)
    ids = set()
    for k in range(20):
        t = k * 0.1
        boxes = np.array([[100 + 30 * k, 100, 160 + 30 * k, 140],  # moving right
                          [800 - 25 * k, 300, 860 - 25 * k, 340]], np.float32)  # moving left
        det = Detections(boxes, np.array([0.9, 0.9], np.float32), np.array([Category.CAR, Category.TRUCK], np.int16))
        if k == 10:  # one missed detection must not create a new vehicle
            det = det.subset(np.array([True, False]))
        obs, _ = tr.update(det, t)
        ids.update(o[0] for o in obs)
    assert len(ids) == 2


def test_direction_clustering_and_names():
    angles = np.concatenate([np.random.default_rng(1).normal(0, 8, 40) % 360,
                             np.random.default_rng(2).normal(180, 8, 30)])
    centers = cluster_directions(angles)
    assert len(centers) == 2
    assert direction_text(0)[1] == "van links naar rechts in beeld"
    assert "noord" not in " ".join(direction_text(a)[1] for a in range(0, 360, 45))


def test_postprocess_relative_movement_and_stationary():
    rows = []
    frames = np.arange(0, 250, 2)
    for f in frames:
        t = f / 25
        rows.append((f, t, 1, 50 + 40 * t, 100, 110 + 40 * t, 130, 0, 0.9))  # moving car
        rows.append((f, t, 2, 400, 300, 460, 330, 0, 0.9))  # never moves (parked)
        if t < 5:
            rows.append((f, t, 3, 600 - 80 * t, 200, 660 - 80 * t, 230, 1, 0.9))
        else:  # stops for 5 s
            rows.append((f, t, 3, 200, 200, 260, 230, 1, 0.9))
    a = np.array(rows, np.float64)
    raw = {"frame": a[:, 0].astype(np.int64), "t": a[:, 1], "track": a[:, 2].astype(np.int64),
           "box": a[:, 3:7].astype(np.float32), "cat": a[:, 7].astype(np.int16), "score": a[:, 8].astype(np.float32)}
    obs, tracks, info = postprocess(raw, frames, frames / 25, 25, 1000, 500, 10)
    by_id = {int(i): k for k, i in enumerate(tracks["id"])}
    assert tracks["static"][by_id[2]] and not tracks["moving"][by_id[2]]
    assert tracks["moving"][by_id[1]] and tracks["moving"][by_id[3]]
    st3 = obs["state"][(obs["track"] == 3) & (obs["t"] > 6)]
    assert (st3 == STATE_STATIONARY).mean() > 0.9
    # the faster car (relative to its size) gets a higher relative value
    r1 = np.median(obs["rel"][(obs["track"] == 1)])
    r3 = np.median(obs["rel"][(obs["track"] == 3) & (obs["t"] < 4)])
    assert r3 > r1
    assert len(info["directions"]) == 2


@pytest.mark.skipif(not __import__("traffic_snapshot.analysis.detection", fromlist=["x"]).available_models(),
                    reason="detectiemodellen niet aanwezig (scripts/fetch_models.py)")
def test_neural_detector_runs_and_maps_categories(synthetic_video):
    import cv2

    from traffic_snapshot.analysis.detection import HybridDetector, MotionDetector, YoloxDetector

    det = YoloxDetector("yolox_s", tiles=(2, 1))
    cap = cv2.VideoCapture(str(synthetic_video))
    hybrid = HybridDetector(det, MotionDetector(expected_vehicle_px=45))
    out = None
    for _ in range(40):
        ok, frame = cap.read()
        assert ok
        out = hybrid.detect(frame)
    assert out.boxes.shape[1] == 4
    assert set(out.cats.tolist()) <= {int(c) for c in Category}
    assert det.describe()["model_sha256"]
