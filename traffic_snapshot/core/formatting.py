"""Human-friendly formatting helpers (Dutch conventions)."""

from __future__ import annotations

from datetime import datetime


def fmt_duration(seconds: float, *, always_hours: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h or always_hours:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def fmt_timecode(seconds: float) -> str:
    """mm:ss.t (or h:mm:ss.t) for precise positions in the player."""
    seconds = max(0.0, float(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{s:04.1f}"
    return f"{int(m):02d}:{s:04.1f}"


def parse_timecode(text: str) -> float | None:
    """Parse 'ss', 'mm:ss', 'h:mm:ss' (decimals allowed). Returns None if invalid."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def fmt_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(n)
    for u in units:
        if n < 1024 or u == units[-1]:
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} TB"


def fmt_number(value: float, decimals: int = 0) -> str:
    text = f"{value:,.{decimals}f}"
    # Dutch: thousands '.' and decimal ','.
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def fmt_datetime(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%d-%m-%Y %H:%M")
