"""User-facing error types.

Every error raised towards the UI carries a plain-language Dutch message for
the normal user, plus optional technical details for diagnosis. The UI shows
``title``/``message``/``hint`` and hides ``detail`` behind a "Technische
details" toggle.
"""

from __future__ import annotations


class AppError(Exception):
    title = "Er is een probleem opgetreden"

    def __init__(self, message: str, *, hint: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def __str__(self) -> str:
        return self.message if not self.detail else f"{self.message} ({self.detail})"


class VideoOpenError(AppError):
    title = "Video kan niet worden geopend"


class UnsupportedFormatError(AppError):
    title = "Videoformaat wordt niet ondersteund"


class CorruptVideoError(AppError):
    title = "Beschadigde videodata"


class DiskSpaceError(AppError):
    title = "Onvoldoende opslagruimte"


class ResourceError(AppError):
    title = "Onvoldoende systeemresources"


class AnalysisError(AppError):
    title = "Analyse kan niet worden uitgevoerd"


class AnalysisCancelled(AppError):
    title = "Analyse is onderbroken"

    def __init__(self, message: str = "De analyse is gestopt. De voortgang is bewaard.", **kw) -> None:
        super().__init__(message, **kw)


class ProjectError(AppError):
    title = "Project kan niet worden geopend"
