"""Processing strategy: how many frames to analyse and at what detail.

The user's two settings are translated into technical parameters here — and
nowhere are they used to compute a speed.

Frame sampling
    For reliable tracking an object should move less than roughly 40 % of its
    own length between two analysed frames. With a typical vehicle length of
    ~4 m and the expected speed *v*, the maximum time step is
    ``dt = 0.4 * 4 m / v``. Note that the scene width cancels out: a wider
    scene makes vehicles both smaller and slower in pixels.

Detector resolution
    The scene width gives the approximate size of a vehicle in pixels. If
    vehicles would become too small at the detector's input resolution, the
    larger model and/or image tiling is chosen; if they are too small even
    then, background-subtraction motion detection is used instead.

Adaptive refinement
    :class:`AdaptiveSampler` watches the tracker's stability statistics per
    window of video. When objects jump too far between frames or tracks
    fragment, the window is re-processed with a smaller frame step. When
    tracking is comfortably stable, the step is carefully relaxed again.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from traffic_snapshot.core.formatting import fmt_number
from traffic_snapshot.core.models import AnalysisSettings, VideoInfo

VEHICLE_LENGTH_M = 4.0
MAX_STEP_FRACTION = 0.4  # of vehicle length per analysed frame
MAX_DT_S = 0.5  # never analyse fewer than 2 frames per second
WINDOW_S = 3.0  # adaptive-control window length


@dataclass
class ProcessingPlan:
    fps: float
    base_stride: int
    max_stride: int
    detector: str  # "yolox_s" | "yolox_tiny" | "motion"
    tiles: tuple[int, int]
    expected_vehicle_px: float
    vehicle_px_at_detector: float
    window_frames: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tiles"] = list(self.tiles)
        return d

    def describe_for_user(self) -> str:
        if self.base_stride <= 1:
            sampling = "Alle beelden van de video worden geanalyseerd."
        else:
            per_s = fmt_number(self.fps / self.base_stride, 1)
            sampling = (
                f"Ongeveer {per_s} beelden per seconde worden geanalyseerd (elk {self.base_stride}e beeld). "
                "Waar het volgen van voertuigen onvoldoende stabiel is, worden automatisch meer beelden gebruikt."
            )
        if self.detector == "motion":
            det = "Voertuigen worden herkend op basis van beweging; het type voertuig wordt niet bepaald."
        else:
            det = "Voertuigen worden herkend met een lokaal detectiemodel dat ook het type voertuig inschat."
            if self.tiles[0] * self.tiles[1] > 1:
                det += " Omdat voertuigen klein in beeld zijn, wordt het beeld in delen extra nauwkeurig bekeken."
        return f"{sampling}\n{det}"


def build_plan(video: VideoInfo, settings: AnalysisSettings, models: list[str]) -> ProcessingPlan:
    fps = video.fps
    speed_ms = max(settings.expected_speed_kmh, 10) / 3.6
    dt = min(MAX_DT_S, MAX_STEP_FRACTION * VEHICLE_LENGTH_M / speed_ms)
    base_stride = max(1, int(math.floor(dt * fps)))
    max_stride = max(base_stride, int(math.floor(MAX_DT_S * fps)))
    max_stride = min(max_stride, base_stride * 2)

    frame_long = max(video.width, video.height)
    px_per_m = video.width / max(settings.scene_width_m, 1)
    # Vehicles are rarely seen along their full length; use the mean of length and width.
    vehicle_px = px_per_m * (VEHICLE_LENGTH_M + 1.8) / 2

    notes: list[str] = []
    detector = "motion"
    tiles = (1, 1)
    at_det = vehicle_px
    if models:
        def size_at(model: str, t: tuple[int, int]) -> float:
            inp = 640 if model == "yolox_s" else 416
            region = frame_long / max(t)  # tiles cut the long side
            return vehicle_px * min(1.0, inp / region)

        # Reliability first: the larger model whenever it is available.
        detector = "yolox_s" if "yolox_s" in models else models[0]
        for t in [(1, 1), (2, 1), (2, 2), (3, 2)]:
            tiles = t
            if size_at(detector, t) >= 14:
                break
        at_det = size_at(detector, tiles)
        if at_det < 8:
            detector, tiles = "motion", (1, 1)
            at_det = vehicle_px
            notes.append("Voertuigen zijn zeer klein in beeld; bewegingsdetectie wordt gebruikt en typen worden niet onderscheiden.")
        elif at_det < 14:
            notes.append("Voertuigen zijn klein in beeld; detectie kan onvolledig zijn.")
        if video.width * video.height * tiles[0] * tiles[1] > 3840 * 2160 * 2:
            notes.append("Zeer hoge resolutie: de analyse kan lang duren.")
    else:
        notes.append("Geen detectiemodel gevonden; bewegingsdetectie wordt gebruikt en typen worden niet onderscheiden.")

    window_frames = max(base_stride * 4, int(round(WINDOW_S * fps)))
    return ProcessingPlan(
        fps=fps,
        base_stride=base_stride,
        max_stride=max_stride,
        detector=detector,
        tiles=tiles,
        expected_vehicle_px=round(vehicle_px, 1),
        vehicle_px_at_detector=round(at_det, 1),
        window_frames=window_frames,
        notes=notes,
    )


@dataclass
class WindowStats:
    matched: int = 0
    step_ratios: list[float] = field(default_factory=list)
    births_interior: int = 0
    births: int = 0

    def add(self, s) -> None:
        self.matched += s.matched
        self.step_ratios.extend(s.step_ratios)
        self.births_interior += s.births_interior
        self.births += s.births

    @property
    def step_p75(self) -> float:
        return float(np.percentile(self.step_ratios, 75)) if self.step_ratios else 0.0

    @property
    def fragmentation(self) -> float:
        return self.births_interior / max(1, self.matched)


@dataclass
class AdaptiveSampler:
    """Decides, per window, whether to refine (re-process) or relax the frame step."""

    stride: int
    base_stride: int
    max_stride: int
    refine_step: float = 0.55  # objects jump > 55 % of their size -> too coarse
    refine_frag: float = 0.12
    relax_step: float = 0.2
    stable_windows: int = 0
    refinements: int = 0
    relaxations: int = 0

    def decide(self, w: WindowStats) -> str:
        """Returns 'refine', 'relax' or 'keep' and updates the stride."""
        enough = w.matched >= 4
        unstable = enough and (w.step_p75 > self.refine_step or w.fragmentation > self.refine_frag)
        if unstable and self.stride > 1:
            self.stride = max(1, self.stride // 2)
            self.stable_windows = 0
            self.refinements += 1
            return "refine"
        if enough and w.step_p75 < self.relax_step and w.fragmentation < self.refine_frag / 2:
            self.stable_windows += 1
        else:
            self.stable_windows = 0
        if self.stable_windows >= 3 and self.stride < self.max_stride:
            self.stride = min(self.max_stride, self.stride * 2)
            self.stable_windows = 0
            self.relaxations += 1
            return "relax"
        return "keep"
