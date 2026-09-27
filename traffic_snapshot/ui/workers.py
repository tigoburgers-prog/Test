"""Background workers. Heavy work never runs on the GUI thread."""

from __future__ import annotations

import logging
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from traffic_snapshot.analysis.pipeline import AnalysisPipeline, Progress
from traffic_snapshot.core.errors import AnalysisCancelled, AnalysisError, AppError
from traffic_snapshot.core.models import VideoInfo
from traffic_snapshot.core.project_store import ProjectHandle, ProjectStore

log = logging.getLogger(__name__)


def to_app_error(exc: BaseException) -> AppError:
    if isinstance(exc, AppError):
        return exc
    return AnalysisError(
        "Er is een onverwachte fout opgetreden.",
        detail="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-4000:],
    )


class _Runner(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            result = self.fn(self.progress.emit)
        except AnalysisCancelled:
            self.cancelled.emit()
        except BaseException as exc:  # noqa: BLE001 - everything must reach the UI
            log.exception("Background task failed")
            self.failed.emit(to_app_error(exc))
        else:
            self.finished.emit(result)


class Task(QObject):
    """Runs ``fn(report_progress)`` in a QThread and relays signals to the GUI thread."""

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cancel_event = threading.Event()
        self.thread = QThread()
        self.runner = _Runner(fn)
        self.runner.moveToThread(self.thread)
        self.thread.started.connect(self.runner.run)
        self.runner.progress.connect(self.progress)
        self.runner.finished.connect(self._done_ok)
        self.runner.failed.connect(self._done_fail)
        self.runner.cancelled.connect(self._done_cancel)

    def start(self) -> None:
        self.thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def is_running(self) -> bool:
        return self.thread.isRunning()

    def wait(self, ms: int = 30000) -> bool:
        return self.thread.wait(ms)

    def _stop_thread(self) -> None:
        self.thread.quit()
        self.thread.wait(5000)

    def _done_ok(self, result) -> None:
        self._stop_thread()
        self.finished.emit(result)

    def _done_fail(self, err) -> None:
        self._stop_thread()
        self.failed.emit(err)

    def _done_cancel(self) -> None:
        self._stop_thread()
        self.cancelled.emit()


def analysis_task(handle: ProjectHandle, store: ProjectStore, resume: bool, parent: QObject | None = None) -> Task:
    task: Task

    def fn(report):
        pipeline = AnalysisPipeline(handle, store, on_progress=report, cancel_event=task.cancel_event, resume=resume)
        return pipeline.run()

    task = Task(fn, parent)
    return task


def import_task(store: ProjectStore, name: str, video: Path, info: VideoInfo, copy: bool,
                parent: QObject | None = None) -> Task:
    task: Task

    def fn(report):
        return store.create_project(
            name, video, info, copy_video=copy,
            progress=lambda f: report(Progress(f, "import", "Video kopiëren…")),
            cancelled=task.cancel_event.is_set,
        )

    task = Task(fn, parent)
    return task
