"""End-to-end analysis on a synthetic video (motion detector, no model needed)."""

import threading

import numpy as np
import pytest

from tests.conftest import expected_counts
from traffic_snapshot.analysis.pipeline import AnalysisPipeline
from traffic_snapshot.analysis.results import AnalysisResults, Region
from traffic_snapshot.core.errors import AnalysisCancelled
from traffic_snapshot.core.models import ProjectStatus
from traffic_snapshot.core.project_store import ProjectStore
from traffic_snapshot.core.video import probe_video


def _project(home, video, name="syn"):
    store = ProjectStore(home / "Projecten")
    h = store.create_project(name, video, probe_video(video), copy_video=False)
    h.project.settings.expected_speed_kmh = 50
    h.project.settings.scene_width_m = 20
    store.save(h)
    return store, h


def test_full_analysis(home, synthetic_video):
    store, h = _project(home, synthetic_video)
    progress = []
    res = AnalysisPipeline(h, store, on_progress=progress.append, detector_override="motion").run()
    lane1, lane2 = expected_counts()
    sel = res.select()
    k = sel.kpis()
    assert abs(k["movements"] - (lane1 + lane2)) <= 2
    dirs = sel.counts_by_direction()
    assert len(res.directions) == 2
    assert sorted(v for d, v in dirs.items() if d >= 0) == pytest.approx(sorted([lane1, lane2]), abs=1)
    assert k["stationary_share"] > 0.03  # the queue at the stop line
    assert sel.stationary_episodes(1.0)
    # progress is monotonic and completes
    fr = [p.fraction for p in progress]
    assert fr[-1] == 1.0 and all(b >= a - 1e-9 for a, b in zip(fr, fr[1:]))
    # provenance + persistence
    reloaded = store.load(h.path)
    assert reloaded.project.status == ProjectStatus.COMPLETED
    assert reloaded.project.analysis.method_version
    again = AnalysisResults.load(h.results_dir)
    assert again.select().kpis()["movements"] == k["movements"]
    assert (h.results_dir / "background.jpg").is_file()
    # selections: time range and zone
    first_half = res.select(0, 7).kpis()["movements"]
    assert 0 < first_half < k["movements"]
    lower = res.select(region=Region(0, 0.5, 1, 1)).counts_by_direction()
    assert sum(v for d, v in lower.items() if d >= 0) <= lane2 + 1
    for kind in ("density", "movement", "variability", "stagnation"):
        g = sel.heatmap(kind)
        assert g.shape == (res.grid_rows, res.grid_cols)
    assert np.nanmax(sel.heatmap("stagnation")) > 0


def test_interrupt_and_resume(home, synthetic_video):
    store, h = _project(home, synthetic_video, "resume")
    cancel = threading.Event()

    def on_progress(p):
        if p.stage == "frames" and p.fraction > 0.4:
            cancel.set()

    with pytest.raises(AnalysisCancelled):
        AnalysisPipeline(h, store, on_progress=on_progress, cancel_event=cancel, detector_override="motion").run()
    h = store.load(h.path)
    assert h.project.status == ProjectStatus.INTERRUPTED
    assert 0.3 < h.project.progress < 0.95

    res = AnalysisPipeline(h, store, detector_override="motion", resume=True).run()
    lane1, lane2 = expected_counts()
    assert abs(res.select().kpis()["movements"] - (lane1 + lane2)) <= 3
    assert store.load(h.path).project.analysis.resumed_count == 1


def test_missing_video_gives_clear_error(home, synthetic_video, tmp_path):
    import shutil

    from traffic_snapshot.core.errors import VideoOpenError

    copy = tmp_path / "weg.mp4"
    shutil.copy(synthetic_video, copy)
    store, h = _project(home, copy, "weg")
    copy.unlink()
    with pytest.raises(VideoOpenError):
        AnalysisPipeline(h, store, detector_override="motion").run()
    assert store.load(h.path).project.status == ProjectStatus.FAILED
