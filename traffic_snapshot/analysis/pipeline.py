"""The analysis pipeline: video → detections → tracks → relative analysis.

Runs entirely locally in a background thread (see ``ui/workers.py``); all
heavy native work (decoding, ONNX inference) releases the GIL so the user
interface stays responsive.

Robustness
    * work is committed to SQLite per window (a few seconds of video) together
      with a checkpoint of the tracker/sampler state; a stop, crash or power
      loss loses at most one window;
    * an interrupted analysis resumes from the last checkpoint if the video
      and settings are unchanged;
    * unreadable stretches of video are skipped and reported instead of
      aborting the whole analysis.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np

from traffic_snapshot import ANALYSIS_METHOD_VERSION, __version__
from traffic_snapshot.analysis.detection import HybridDetector, MotionDetector, YoloxDetector, available_models
from traffic_snapshot.analysis.postprocess import postprocess
from traffic_snapshot.analysis.results import AnalysisResults, detect_events
from traffic_snapshot.analysis.store import AnalysisStore, Checkpoint
from traffic_snapshot.analysis.strategy import AdaptiveSampler, ProcessingPlan, WindowStats, build_plan
from traffic_snapshot.analysis.tracking import Tracker
from traffic_snapshot.core.errors import (
    AnalysisCancelled,
    AnalysisError,
    AppError,
    CorruptVideoError,
    ResourceError,
    VideoOpenError,
)
from traffic_snapshot.core.models import AnalysisRun, ProjectStatus, now_iso
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore, ensure_free_space
from traffic_snapshot.core.video import open_capture

log = logging.getLogger(__name__)

STAGES: list[tuple[str, str]] = [
    ("prepare", "Video voorbereiden"),
    ("frames", "Frames verwerken"),
    ("detect", "Objecten detecteren"),
    ("track", "Objecten volgen"),
    ("motion", "Beweging analyseren"),
    ("spatial", "Ruimtelijke analyse"),
    ("results", "Resultaten verwerken"),
    ("save", "Analyse opslaan"),
]
FRAME_STAGES = ("frames", "detect", "track")
LOOP_START, LOOP_END = 0.03, 0.90


@dataclass
class Progress:
    fraction: float
    stage: str
    message: str
    active: tuple[str, ...] = ()
    video_pos_s: float = 0.0
    video_total_s: float = 0.0
    eta_s: float | None = None
    stride: int = 1
    device: str = ""
    extra: dict = field(default_factory=dict)


class _FrameSource:
    """Sequential/seeking frame access with tolerance for damaged stretches."""

    def __init__(self, path: str, frame_count: int, fps: float) -> None:
        self.cap = open_capture(path)
        if not self.cap.isOpened():
            raise VideoOpenError("De video kan niet (meer) worden geopend.", detail=path)
        self.pos = 0
        self.frame_count = frame_count
        self.fps = fps
        self.skipped_ranges: list[tuple[int, int]] = []

    def close(self) -> None:
        self.cap.release()

    def _read(self, i: int) -> np.ndarray | None:
        if i < self.pos or i - self.pos > 45:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            self.pos = i
        while self.pos < i:
            if not self.cap.grab():
                return None
            self.pos += 1
        ok, frame = self.cap.read()
        self.pos += 1
        return frame if ok else None

    def read(self, i: int) -> tuple[int, np.ndarray | None]:
        """Returns (actual_index, frame). Frame None means end of readable video."""
        frame = self._read(i)
        if frame is not None:
            return i, frame
        # Near the declared end: container metadata often overestimates length.
        if i >= self.frame_count - max(3, int(self.fps)):
            return i, None
        step = max(1, int(self.fps))
        j = i
        for _ in range(10):
            j += step
            if j >= self.frame_count:
                break
            self.pos = -10**9  # force seek
            frame = self._read(j)
            if frame is not None:
                self.skipped_ranges.append((i, j))
                log.warning("Unreadable frames %d..%d skipped", i, j)
                return j, frame
        raise CorruptVideoError(
            "Een deel van de video is beschadigd en kan niet worden gelezen.",
            hint="De analyse is bewaard tot het beschadigde deel. Probeer eventueel een herstelde kopie van de video.",
            detail=f"unreadable from frame {i}",
        )


def make_detector(plan: ProcessingPlan) -> tuple[object, list[str]]:
    notes: list[str] = []
    if plan.detector != "motion":
        try:
            neural = YoloxDetector(plan.detector, tiles=plan.tiles)
            return HybridDetector(neural, _motion(plan)), notes
        except Exception as exc:  # model missing/corrupt or runtime failure
            log.exception("Neural detector unavailable")
            notes.append(f"Detectiemodel kon niet worden geladen ({exc.__class__.__name__}); bewegingsdetectie gebruikt.")
            plan.detector, plan.tiles = "motion", (1, 1)
    return _motion(plan), notes


def _motion(plan: ProcessingPlan) -> MotionDetector:
    analysed_fps = plan.fps / max(1, plan.base_stride)
    return MotionDetector(expected_vehicle_px=plan.expected_vehicle_px, history_frames=max(200, int(60 * analysed_fps)))


class AnalysisPipeline:
    def __init__(
        self,
        handle: ProjectHandle,
        store: ProjectStore,
        on_progress: Callable[[Progress], None] | None = None,
        cancel_event: threading.Event | None = None,
        *,
        resume: bool = True,
        detector_override: str | None = None,
    ) -> None:
        self.h = handle
        self.store = store
        self.on_progress = on_progress or (lambda p: None)
        self.cancel = cancel_event or threading.Event()
        self.resume = resume
        self.detector_override = detector_override  # tests / diagnostics
        self._last_emit = 0.0
        self._last_meta_save = 0.0

    # ------------------------------------------------------------------ helpers
    def _emit(self, p: Progress, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_emit >= 0.2:
            self._last_emit = now
            self.on_progress(p)

    def _set_status(self, status: ProjectStatus, error: str = "") -> None:
        self.h.project.status = status
        self.h.project.last_error = error
        self.store.save(self.h)

    def _settings_key(self) -> dict:
        v = self.h.project.video
        return {
            "settings": self.h.project.settings.__dict__,
            "video": {"file": v.file_name, "size": v.size_bytes, "frames": v.frame_count},
            "method": ANALYSIS_METHOD_VERSION,
            "detector_override": self.detector_override,
        }

    # ------------------------------------------------------------------ main
    def run(self) -> AnalysisResults:
        project = self.h.project
        try:
            self._set_status(ProjectStatus.ANALYZING)
            result = self._run()
            project.progress = 1.0
            self._set_status(ProjectStatus.COMPLETED)
            return result
        except AnalysisCancelled:
            self._set_status(ProjectStatus.INTERRUPTED, "De analyse is gestopt door de gebruiker.")
            raise
        except AppError as exc:
            self._set_status(ProjectStatus.INTERRUPTED if isinstance(exc, CorruptVideoError) else ProjectStatus.FAILED,
                             exc.message)
            raise
        except MemoryError as exc:
            self._set_status(ProjectStatus.INTERRUPTED, "Onvoldoende werkgeheugen.")
            raise ResourceError(
                "Er is onvoldoende werkgeheugen om de analyse uit te voeren.",
                hint="Sluit andere programma's en hervat de analyse; de voortgang is bewaard.",
                detail=repr(exc),
            ) from exc
        except Exception as exc:
            log.exception("Analysis failed")
            self._set_status(ProjectStatus.FAILED, "Onverwachte fout tijdens de analyse.")
            raise AnalysisError(
                "De analyse kon door een onverwachte fout niet worden voltooid.",
                hint="Probeer de analyse te hervatten. Blijft het probleem bestaan, bekijk dan de technische details.",
                detail=f"{exc.__class__.__name__}: {exc}",
            ) from exc

    def _run(self) -> AnalysisResults:
        h, project = self.h, self.h.project
        video = project.video
        total_s = video.duration_s
        self._emit(Progress(0.0, "prepare", "Video voorbereiden…", ("prepare",), 0, total_s), force=True)

        if not h.video_file.is_file():
            raise VideoOpenError(
                "Het videobestand van dit project is niet gevonden.",
                hint="Als de video niet naar de projectmap is gekopieerd, zet het bestand dan terug op de oorspronkelijke plek.",
                detail=str(h.video_file),
            )
        ensure_free_space(h.path, 300 * 1024 * 1024, "de analyse")

        models = available_models()
        plan = build_plan(video, project.settings, models if self.detector_override != "motion" else [])
        store = AnalysisStore(h.db_path)
        try:
            cp = store.load_checkpoint() if self.resume else None
            if cp is not None and store.get_meta("run_key") != self._settings_key():
                log.info("Checkpoint does not match current settings; starting fresh")
                cp = None
            if cp is None:
                store.close()
                for suffix in ("", "-wal", "-shm"):
                    p = h.db_path.with_name(h.db_path.name + suffix)
                    if p.exists():
                        p.unlink()
                store = AnalysisStore(h.db_path)
                store.set_meta("run_key", self._settings_key())
                store.conn.commit()
                resumed = False
            else:
                resumed = True
                plan = pickle.loads(cp.state["plan"]) if "plan" in cp.state else plan

            detector, det_notes = make_detector(plan)
            plan.notes.extend(n for n in det_notes if n not in plan.notes)
            device = getattr(detector, "device_label", "CPU")

            run = project.analysis if (resumed and project.analysis) else None
            if run is None:
                run = AnalysisRun(
                    app_version=__version__,
                    method_version=ANALYSIS_METHOD_VERSION,
                    settings=dict(project.settings.__dict__),
                    detector=detector.describe(),
                    tracker={},
                    strategy=plan.to_dict(),
                    compute_device=device,
                    started_at=now_iso(),
                )
            else:
                run.resumed_count += 1
                run.compute_device = device
            project.analysis = run
            self.store.save(h)

            t_start = time.monotonic()
            self._frame_loop(store, plan, detector, cp, device)
            run.processing_seconds += time.monotonic() - t_start
            return self._finish(store, plan, run, device)
        finally:
            store.close()

    # ------------------------------------------------------------------ frame loop
    def _frame_loop(self, store: AnalysisStore, plan: ProcessingPlan, detector, cp: Checkpoint | None,
                    device: str) -> None:
        video = self.h.project.video
        fps, n_frames = video.fps, video.frame_count
        if cp is not None:
            tracker: Tracker = pickle.loads(cp.state["tracker"])
            sampler: AdaptiveSampler = pickle.loads(cp.state["sampler"])
            cur = cp.next_frame
            skipped = list(cp.state.get("skipped", []))
        else:
            tracker = Tracker(video.width, video.height)
            sampler = AdaptiveSampler(plan.base_stride, plan.base_stride, plan.max_stride)
            cur = 0
            skipped = []
        self.h.project.analysis.tracker = tracker.describe()

        src = _FrameSource(str(self.h.video_file), n_frames, fps)
        if cp is not None:
            self._warm_up_motion_model(detector, src, cur, sampler.stride)
        warmup_until = cur + max(2 * plan.base_stride, int(fps))
        rate_hist: list[tuple[float, float]] = []

        def checkpoint_state() -> dict:
            return {
                "tracker": pickle.dumps(tracker),
                "sampler": pickle.dumps(sampler),
                "plan": pickle.dumps(plan),
                "skipped": skipped + src.skipped_ranges,
            }

        try:
            window_start = cur
            snapshot = (pickle.dumps(tracker), pickle.dumps(sampler))
            window = WindowStats()
            buf_obs: list[tuple] = []
            buf_frames: list[tuple] = []
            buf_log: list[tuple] = []
            refines_this_window = 0
            end_reached = False

            while not end_reached:
                if self.cancel.is_set():
                    store.commit_batch(buf_obs, buf_frames, buf_log, Checkpoint(cur, checkpoint_state()))
                    raise AnalysisCancelled()
                if cur >= n_frames:
                    end_reached = True
                else:
                    actual, frame = src.read(cur)
                    if frame is None:
                        end_reached = True
                    else:
                        cur = actual
                        t = cur / fps
                        det = detector.detect(frame)
                        observed, st = tracker.update(det, t)
                        if cur < warmup_until:
                            st.births_interior = 0
                        window.add(st)
                        for tid, box, cat, score in observed:
                            buf_obs.append((cur, t, tid, float(box[0]), float(box[1]), float(box[2]), float(box[3]),
                                            cat, score))
                        buf_frames.append((cur, t, len(det), sampler.stride))
                        cur += sampler.stride

                window_done = end_reached or cur >= window_start + plan.window_frames
                if window_done:
                    decision = sampler.decide(window) if not end_reached else "keep"
                    if decision == "refine" and refines_this_window < 3 and buf_frames:
                        # Re-process this window at the finer step, from the saved state.
                        buf_log.clear()
                        stride = sampler.stride
                        tracker = pickle.loads(snapshot[0])
                        sampler_state = pickle.loads(snapshot[1])
                        sampler_state.stride = stride
                        sampler_state.refinements = sampler.refinements
                        sampler = sampler_state
                        buf_obs.clear()
                        buf_frames.clear()
                        buf_log.append((window_start, window_start / fps, "refine",
                                        f"stride→{stride} (p75 stap={window.step_p75:.2f}, frag={window.fragmentation:.2f})"))
                        cur = window_start
                        window = WindowStats()
                        refines_this_window += 1
                        continue
                    if decision == "relax":
                        buf_log.append((cur, cur / fps, "relax", f"stride→{sampler.stride}"))
                    store.commit_batch(buf_obs, buf_frames, buf_log, Checkpoint(cur, checkpoint_state()))
                    buf_obs, buf_frames, buf_log = [], [], []
                    window_start = cur
                    snapshot = (pickle.dumps(tracker), pickle.dumps(sampler))
                    window = WindowStats()
                    refines_this_window = 0

                    frac = min(1.0, cur / max(1, n_frames))
                    self.h.project.progress = LOOP_START + frac * (LOOP_END - LOOP_START)
                    now = time.monotonic()
                    if now - self._last_meta_save > 5:
                        self._last_meta_save = now
                        self.store.save(self.h)

                # progress + ETA (based on recent throughput)
                frac = min(1.0, cur / max(1, n_frames))
                now = time.monotonic()
                rate_hist.append((now, frac))
                while len(rate_hist) > 2 and now - rate_hist[0][0] > 30:
                    rate_hist.pop(0)
                eta = None
                if len(rate_hist) >= 2 and now - rate_hist[0][0] > 4 and frac > 0.02:
                    df = rate_hist[-1][1] - rate_hist[0][1]
                    if df > 0:
                        eta = (1 - frac) * (now - rate_hist[0][0]) / df * 1.08 + 5
                pos_s = min(cur, n_frames) / fps
                self._emit(Progress(
                    LOOP_START + frac * (LOOP_END - LOOP_START), "frames",
                    f"Beeld {min(cur, n_frames):,} van {n_frames:,} verwerkt".replace(",", "."),
                    FRAME_STAGES, pos_s, video.duration_s, eta, sampler.stride, device,
                ))
        finally:
            src.close()
        self.h.project.analysis.notes = list(dict.fromkeys(
            self.h.project.analysis.notes + plan.notes + (
                [f"{len(skipped + src.skipped_ranges)} onleesbaar deel/delen van de video overgeslagen."]
                if skipped or src.skipped_ranges else []
            )
        ))

    @staticmethod
    def _warm_up_motion_model(detector, src: _FrameSource, cur: int, stride: int) -> None:
        """The background model is not part of the checkpoint: rebuild it from preceding frames."""
        motion = detector if isinstance(detector, MotionDetector) else getattr(detector, "motion", None)
        if motion is None or cur <= 0:
            return
        n = min(120, motion.history_frames)
        for i in range(max(0, cur - n * stride), cur, stride):
            _, frame = src.read(i)
            if frame is None:
                break
            motion.detect(frame)

    # ------------------------------------------------------------------ post-processing
    def _finish(self, store: AnalysisStore, plan: ProcessingPlan, run: AnalysisRun, device: str) -> AnalysisResults:
        video = self.h.project.video
        total = video.duration_s

        def stage(key: str, frac: float, msg: str) -> None:
            if self.cancel.is_set():
                raise AnalysisCancelled()
            self._emit(Progress(frac, key, msg, (key,), total, total, None, 0, device), force=True)

        stage("motion", 0.91, "Relatieve beweging berekenen…")
        raw = store.observations_array()
        frame_idx, frame_t, strides = store.frames_array()
        obs, tracks, info = postprocess(raw, frame_idx, frame_t, video.fps, video.width, video.height, total)
        obs["processed_frames"] = frame_idx

        stage("spatial", 0.94, "Ruimtelijke patronen bepalen…")
        background = self._background_image()

        stage("results", 0.96, "Gebeurtenissen en kerncijfers bepalen…")
        strat_log = store.strategy_log()
        summary = {
            "video": {"duration_s": total, "fps": video.fps, "width": video.width, "height": video.height,
                      "frame_count": video.frame_count, "file_name": video.file_name},
            "settings": dict(self.h.project.settings.__dict__),
            "plan": plan.to_dict(),
            "directions": info["directions"],
            "reference_speed_norm": info["reference_speed_norm"],
            "thresholds": info["thresholds"],
            "quality": {
                "processed_frames": int(len(frame_idx)),
                "processed_share": float(len(frame_idx) / max(1, video.frame_count)),
                "mean_stride": float(strides.mean()) if len(strides) else 0.0,
                "refinements": sum(1 for r in strat_log if r["event"] == "refine"),
                "relaxations": sum(1 for r in strat_log if r["event"] == "relax"),
                "raw_tracks": info["n_raw_tracks"],
                "valid_tracks": info["n_tracks"],
            },
            "strategy_log": strat_log[-500:],
            "events": [],
            "provenance": {},
        }
        res = AnalysisResults(obs, tracks, summary, self.h.results_dir)
        summary["events"] = detect_events(res)
        res.events = summary["events"]

        stage("save", 0.98, "Resultaten opslaan…")
        run.finished_at = now_iso()
        run.strategy = plan.to_dict()
        run.compute_device = device
        summary["provenance"] = {
            "app_version": run.app_version,
            "method_version": run.method_version,
            "detector": run.detector,
            "tracker": run.tracker,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "processing_seconds": round(run.processing_seconds, 1),
            "compute_device": device,
            "resumed_count": run.resumed_count,
            "notes": run.notes,
            "opencv": cv2.__version__,
            "numpy": np.__version__,
        }
        # write to a temp folder, then swap, so a crash never leaves half results
        tmp_dir = self.h.results_dir.with_name("results.tmp")
        if tmp_dir.exists():
            import shutil

            shutil.rmtree(tmp_dir)
        res.save(tmp_dir)
        if background is not None:
            ok, enc = cv2.imencode(".jpg", background, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                enc.tofile(str(tmp_dir / "background.jpg"))
        if self.h.results_dir.exists():
            import shutil

            shutil.rmtree(self.h.results_dir)
        os.replace(tmp_dir, self.h.results_dir)
        self.store.save(self.h)
        self._emit(Progress(1.0, "save", "Analyse gereed", (), total, total, 0, 0, device), force=True)
        return AnalysisResults.load(self.h.results_dir)

    def _background_image(self) -> np.ndarray | None:
        """Median of frames spread over the video ≈ empty-road reference image."""
        video = self.h.project.video
        cap = open_capture(str(self.h.video_file))
        frames = []
        try:
            n = min(25, max(1, video.frame_count))
            for i in np.linspace(0, max(0, video.frame_count - 2), n).astype(int):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
                ok, f = cap.read()
                if ok:
                    if f.shape[1] > 1280:
                        f = cv2.resize(f, (1280, int(f.shape[0] * 1280 / f.shape[1])), interpolation=cv2.INTER_AREA)
                    frames.append(f)
        finally:
            cap.release()
        if not frames:
            return None
        stack = np.stack(frames)
        out = np.empty(stack.shape[1:], np.uint8)
        for r0 in range(0, stack.shape[1], 64):  # chunked: np.median upcasts to float64
            out[r0 : r0 + 64] = np.median(stack[:, r0 : r0 + 64], axis=0)
        return out


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")
