"""Domain model: projects, video metadata, analysis settings and provenance.

Everything here is plain data that serialises to JSON (``project.json``), so
a project folder stays readable and self-describing without the application.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

PROJECT_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ProjectStatus(str, Enum):
    CREATED = "created"
    ANALYZING = "analyzing"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            ProjectStatus.CREATED: "Nog niet geanalyseerd",
            ProjectStatus.ANALYZING: "Analyse bezig",
            ProjectStatus.INTERRUPTED: "Onderbroken (hervatbaar)",
            ProjectStatus.COMPLETED: "Analyse gereed",
            ProjectStatus.FAILED: "Analyse mislukt",
        }[self]


# Expected speed options. They only steer *how often* frames are sampled; they
# are never used to compute a speed. 30 represents "< 30 km/h".
SPEED_OPTIONS: list[tuple[int, str]] = [
    (30, "< 30 km/h"),
    (50, "50 km/h"),
    (80, "80 km/h"),
    (100, "100 km/h"),
    (120, "120 km/h"),
]

# Approximate width of the visible scene. Used to estimate how large vehicles
# appear in the image, which drives detector resolution / tiling choices.
SCENE_WIDTH_OPTIONS: list[tuple[int, str]] = [
    (20, "± 20 m"),
    (50, "± 50 m"),
    (100, "± 100 m"),
    (500, "± 500 m"),
    (1000, "± 1000 m"),
]


@dataclass
class VideoInfo:
    file_name: str
    duration_s: float
    fps: float
    frame_count: int
    width: int
    height: int
    size_bytes: int
    codec: str = ""

    @property
    def resolution_label(self) -> str:
        return f"{self.width} × {self.height}"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> VideoInfo:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class AnalysisSettings:
    expected_speed_kmh: int = 50
    scene_width_m: int = 100

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> AnalysisSettings:
        d = d or {}
        return cls(
            expected_speed_kmh=int(d.get("expected_speed_kmh", 50)),
            scene_width_m=int(d.get("scene_width_m", 100)),
        )

    def speed_label(self) -> str:
        return dict(SPEED_OPTIONS).get(self.expected_speed_kmh, f"{self.expected_speed_kmh} km/h")

    def width_label(self) -> str:
        return dict(SCENE_WIDTH_OPTIONS).get(self.scene_width_m, f"± {self.scene_width_m} m")


@dataclass
class AnalysisRun:
    """Provenance of one analysis: enough to reproduce/trace the result later."""

    app_version: str
    method_version: str
    settings: dict[str, Any]
    detector: dict[str, Any]
    tracker: dict[str, Any]
    strategy: dict[str, Any]
    compute_device: str
    started_at: str
    finished_at: str = ""
    processing_seconds: float = 0.0
    resumed_count: int = 0
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> AnalysisRun | None:
        if not d:
            return None
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Zone:
    """User-defined analysis area in normalised image coordinates (0..1)."""

    id: str
    name: str
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Zone:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def normalized(self) -> Zone:
        return Zone(self.id, self.name, min(self.x0, self.x1), min(self.y0, self.y1),
                    max(self.x0, self.x1), max(self.y0, self.y1))


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    updated_at: str
    video: VideoInfo
    video_path: str  # relative to the project folder when copied, else absolute
    video_copied: bool
    status: ProjectStatus = ProjectStatus.CREATED
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    analysis: AnalysisRun | None = None
    progress: float = 0.0  # last known fraction of analysis done (for the hub)
    last_error: str = ""
    zones: list[Zone] = field(default_factory=list)
    schema_version: int = PROJECT_SCHEMA_VERSION

    @staticmethod
    def new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Project:
        return cls(
            id=d["id"],
            name=d["name"],
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            video=VideoInfo.from_dict(d["video"]),
            video_path=d["video_path"],
            video_copied=bool(d.get("video_copied", True)),
            status=ProjectStatus(d.get("status", "created")),
            settings=AnalysisSettings.from_dict(d.get("settings")),
            analysis=AnalysisRun.from_dict(d.get("analysis")),
            progress=float(d.get("progress", 0.0)),
            last_error=d.get("last_error", ""),
            zones=[Zone.from_dict(z) for z in d.get("zones", [])],
            schema_version=int(d.get("schema_version", PROJECT_SCHEMA_VERSION)),
        )
