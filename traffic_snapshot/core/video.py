"""Video probing and validation (OpenCV / FFmpeg backend)."""

from __future__ import annotations

import logging
from pathlib import Path

import cv2

from traffic_snapshot.core.errors import CorruptVideoError, UnsupportedFormatError, VideoOpenError
from traffic_snapshot.core.models import VideoInfo

log = logging.getLogger(__name__)

# MP4 is the primary format; the others decode through the same FFmpeg backend.
SUPPORTED_EXTENSIONS = (".mp4", ".m4v", ".mov", ".avi", ".mkv")


def _fourcc_to_str(value: float) -> str:
    code = int(value)
    chars = [chr((code >> (8 * i)) & 0xFF) for i in range(4)]
    text = "".join(chars).strip("\x00 ")
    return text if text.isprintable() else ""


def open_capture(path: str | Path) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(str(path))
    return cap


def probe_video(path: str | Path) -> VideoInfo:
    """Read metadata and verify the file actually decodes.

    Raises a user-facing :class:`AppError` subclass when the file cannot be
    used, so the import screen can explain what is wrong.
    """
    p = Path(path)
    if not p.is_file():
        raise VideoOpenError("Het gekozen bestand bestaat niet (meer).", detail=str(p))
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Bestanden van het type '{p.suffix or '(geen extensie)'}' worden niet ondersteund.",
            hint="Gebruik bij voorkeur een MP4-video. Ook MOV, M4V, AVI en MKV worden geaccepteerd.",
        )
    size = p.stat().st_size
    if size == 0:
        raise CorruptVideoError("Het videobestand is leeg.", detail=str(p))

    cap = open_capture(p)
    if not cap.isOpened():
        raise VideoOpenError(
            "De video kan niet worden geopend.",
            hint="Controleer of het bestand volledig is gekopieerd en in een ander programma afspeelt.",
            detail=f"cv2.VideoCapture failed for {p}",
        )
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        codec = _fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))

        ok, frame = cap.read()
        if not ok or frame is None:
            raise CorruptVideoError(
                "De video bevat geen leesbare beelden.",
                hint="Het bestand is mogelijk beschadigd of gebruikt een codec die niet wordt ondersteund.",
                detail=f"first frame read failed, codec={codec!r}",
            )
        if width <= 0 or height <= 0:
            height, width = frame.shape[:2]
        if not (1.0 <= fps <= 240.0):
            log.warning("Implausible fps %.3f in %s, assuming 25", fps, p)
            fps = 25.0
        if frame_count <= 0:
            frame_count = _count_frames(cap)
        if frame_count <= 1:
            raise CorruptVideoError("De video is te kort om te analyseren.", detail=f"frames={frame_count}")

        # Spot-check the second half: truncated downloads often fail there.
        probe_at = max(0, int(frame_count * 0.9) - 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, probe_at)
        ok, _ = cap.read()
        if not ok:
            log.warning("Frame %d unreadable in %s; video may be truncated", probe_at, p)
    finally:
        cap.release()

    return VideoInfo(
        file_name=p.name,
        duration_s=frame_count / fps,
        fps=fps,
        frame_count=frame_count,
        width=width,
        height=height,
        size_bytes=size,
        codec=codec,
    )


def _count_frames(cap: cv2.VideoCapture) -> int:
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    n = 0
    while cap.grab():
        n += 1
    return n
