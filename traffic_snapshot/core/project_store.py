"""Local project storage.

Each project is a self-contained folder::

    <Naam>_<id>/
        project.json          metadata, settings, status, provenance
        video/<bestand>       copy of the original video (optional, default on)
        analysis/analysis.sqlite   raw tracked observations + resume checkpoints
        results/              derived results (npz/json/jpg), cached for the dashboard
        reports/              generated PDF reports
        exports/              exported images / data

``project.json`` is written atomically (temp file + rename) so a crash can
never leave a half-written metadata file behind.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from traffic_snapshot.core.errors import DiskSpaceError, ProjectError
from traffic_snapshot.core.models import Project, ProjectStatus, VideoInfo, now_iso

log = logging.getLogger(__name__)

PROJECT_FILE = "project.json"


@dataclass
class ProjectHandle:
    project: Project
    path: Path

    @property
    def video_file(self) -> Path:
        p = Path(self.project.video_path)
        return p if p.is_absolute() else self.path / p

    @property
    def analysis_dir(self) -> Path:
        return self.path / "analysis"

    @property
    def db_path(self) -> Path:
        return self.analysis_dir / "analysis.sqlite"

    @property
    def results_dir(self) -> Path:
        return self.path / "results"

    @property
    def reports_dir(self) -> Path:
        d = self.path / "reports"
        d.mkdir(exist_ok=True)
        return d

    @property
    def exports_dir(self) -> Path:
        d = self.path / "exports"
        d.mkdir(exist_ok=True)
        return d

    def has_results(self) -> bool:
        return (self.results_dir / "summary.json").is_file()


@dataclass
class BrokenProject:
    path: Path
    reason: str


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:40] or "Project"


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_free_space(folder: Path, required_bytes: int, what: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if free < required_bytes:
        from traffic_snapshot.core.formatting import fmt_bytes

        raise DiskSpaceError(
            f"Er is niet genoeg vrije schijfruimte voor {what}.",
            hint=f"Nodig: ongeveer {fmt_bytes(required_bytes)}, beschikbaar: {fmt_bytes(free)}. "
            "Maak ruimte vrij of kies ervoor de video niet naar de projectmap te kopiëren.",
            detail=f"free={free} required={required_bytes} folder={folder}",
        )


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ listing
    def list_projects(self) -> tuple[list[ProjectHandle], list[BrokenProject]]:
        handles: list[ProjectHandle] = []
        broken: list[BrokenProject] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            meta = child / PROJECT_FILE
            if not meta.is_file():
                continue
            try:
                handles.append(self.load(child))
            except ProjectError as exc:
                broken.append(BrokenProject(child, exc.message))
        handles.sort(key=lambda h: h.project.created_at, reverse=True)
        return handles, broken

    def load(self, path: Path) -> ProjectHandle:
        meta = Path(path) / PROJECT_FILE
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            project = Project.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProjectError(
                "Het projectbestand is onleesbaar of beschadigd.",
                detail=f"{meta}: {exc!r}",
            ) from exc
        return ProjectHandle(project, Path(path))

    def recover_stale_states(self) -> None:
        """A project left in ANALYZING state means the app stopped unexpectedly."""
        handles, _ = self.list_projects()
        for h in handles:
            if h.project.status == ProjectStatus.ANALYZING:
                h.project.status = ProjectStatus.INTERRUPTED
                h.project.last_error = "De analyse werd onverwacht afgebroken (bijv. door afsluiten of een storing)."
                self.save(h)

    # ------------------------------------------------------------------ writing
    def save(self, handle: ProjectHandle) -> None:
        handle.project.updated_at = now_iso()
        write_json_atomic(handle.path / PROJECT_FILE, handle.project.to_dict())

    def create_project(
        self,
        name: str,
        source_video: Path,
        video_info: VideoInfo,
        *,
        copy_video: bool = True,
        progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> ProjectHandle:
        name = name.strip() or Path(source_video).stem
        project_id = Project.new_id()
        folder = self.root / f"{slugify(name)}_{project_id[-6:]}"
        margin = 50 * 1024 * 1024
        ensure_free_space(self.root, (video_info.size_bytes if copy_video else 0) + margin, "dit project")

        folder.mkdir(parents=True)
        try:
            if copy_video:
                target = folder / "video" / Path(source_video).name
                _copy_with_progress(Path(source_video), target, progress, cancelled)
                video_path = str(target.relative_to(folder))
            else:
                video_path = str(Path(source_video).resolve())
            ts = now_iso()
            project = Project(
                id=project_id,
                name=name,
                created_at=ts,
                updated_at=ts,
                video=video_info,
                video_path=video_path,
                video_copied=copy_video,
            )
            handle = ProjectHandle(project, folder)
            self.save(handle)
            return handle
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def rename(self, handle: ProjectHandle, new_name: str) -> None:
        handle.project.name = new_name.strip() or handle.project.name
        self.save(handle)

    def delete(self, handle_or_path: ProjectHandle | Path) -> None:
        path = handle_or_path.path if isinstance(handle_or_path, ProjectHandle) else Path(handle_or_path)
        path = path.resolve()
        if path.parent != self.root.resolve():
            raise ProjectError("Dit project ligt buiten de projectmap en wordt niet verwijderd.", detail=str(path))
        shutil.rmtree(path)

    def reset_analysis(self, handle: ProjectHandle) -> None:
        """Remove all analysis output so the analysis can start from scratch."""
        for d in (handle.analysis_dir, handle.results_dir):
            if d.exists():
                shutil.rmtree(d)
        handle.project.status = ProjectStatus.CREATED
        handle.project.analysis = None
        handle.project.progress = 0.0
        handle.project.last_error = ""
        self.save(handle)


def _copy_with_progress(
    src: Path,
    dst: Path,
    progress: Callable[[float], None] | None,
    cancelled: Callable[[], bool] | None,
) -> None:
    from traffic_snapshot.core.errors import AnalysisCancelled

    dst.parent.mkdir(parents=True, exist_ok=True)
    total = max(1, src.stat().st_size)
    done = 0
    chunk = 8 * 1024 * 1024
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            if cancelled and cancelled():
                raise AnalysisCancelled("Het importeren is geannuleerd.")
            buf = fin.read(chunk)
            if not buf:
                break
            fout.write(buf)
            done += len(buf)
            if progress:
                progress(done / total)
    shutil.copystat(src, dst)
