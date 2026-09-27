"""Filesystem locations used by the application.

All data stays on the local machine:

* projects live in ``Documenten/Traffic Snapshot/Projecten`` so non-technical
  users can find them with Explorer;
* logs live in the per-user application data folder;
* bundled resources (detection models) are resolved relative to the package,
  or relative to the PyInstaller bundle when running as a frozen executable.

``TRAFFIC_SNAPSHOT_HOME`` overrides the data root (used by tests and for
portable installations).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from traffic_snapshot import APP_NAME


def _documents_dir() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL (5) = "Documents", honours folder redirection.
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:  # pragma: no cover - defensive fallback
            pass
    docs = home / "Documents"
    return docs if docs.exists() else home


def data_root() -> Path:
    override = os.environ.get("TRAFFIC_SNAPSHOT_HOME")
    root = Path(override) if override else _documents_dir() / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_root() -> Path:
    path = data_root() / "Projecten"
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_state_dir() -> Path:
    override = os.environ.get("TRAFFIC_SNAPSHOT_HOME")
    if override:
        path = Path(override) / ".app"
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / "TrafficSnapshot"
    else:
        path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "traffic-snapshot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_root() -> Path:
    """Directory that contains bundled resources such as ``models/``."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def models_dir() -> Path:
    override = os.environ.get("TRAFFIC_SNAPSHOT_MODELS")
    return Path(override) if override else resource_root() / "models"
