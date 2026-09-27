from pathlib import Path

import pytest

from traffic_snapshot.core.errors import CorruptVideoError, ProjectError, UnsupportedFormatError, VideoOpenError
from traffic_snapshot.core.formatting import fmt_bytes, fmt_duration, fmt_number, parse_timecode
from traffic_snapshot.core.models import Project, ProjectStatus, Zone
from traffic_snapshot.core.project_store import ProjectStore
from traffic_snapshot.core.video import probe_video


def test_formatting():
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(75.4) == "1:15"
    assert fmt_duration(3725) == "1:02:05"
    assert parse_timecode("1:30") == 90
    assert parse_timecode("1:02:03,5") == pytest.approx(3723.5)
    assert parse_timecode("abc") is None
    assert fmt_number(1234.5, 1) == "1.234,5"
    assert fmt_bytes(1536 * 1024) == "1,5 MB"


def test_probe_video(synthetic_video):
    info = probe_video(synthetic_video)
    assert info.width == 640 and info.height == 360
    assert info.fps == pytest.approx(25, abs=0.01)
    assert info.duration_s == pytest.approx(14, abs=0.2)


def test_probe_rejects_bad_files(tmp_path):
    with pytest.raises(VideoOpenError):
        probe_video(tmp_path / "bestaat_niet.mp4")
    txt = tmp_path / "notitie.txt"
    txt.write_text("x")
    with pytest.raises(UnsupportedFormatError):
        probe_video(txt)
    bad = tmp_path / "kapot.mp4"
    bad.write_bytes(b"\x00" * 5000)
    with pytest.raises((CorruptVideoError, VideoOpenError)):
        probe_video(bad)


def test_project_lifecycle(home, synthetic_video):
    store = ProjectStore(home / "Projecten")
    info = probe_video(synthetic_video)
    h = store.create_project("Kruispunt A", synthetic_video, info, copy_video=True)
    assert h.video_file.is_file() and h.video_file.parent.parent == h.path
    h.project.zones.append(Zone("z1", "Stopstreep", 0.1, 0.2, 0.3, 0.4))
    h.project.status = ProjectStatus.ANALYZING
    store.save(h)

    handles, broken = store.list_projects()
    assert len(handles) == 1 and not broken
    loaded = handles[0].project
    assert loaded.name == "Kruispunt A" and loaded.zones[0].name == "Stopstreep"

    store.recover_stale_states()  # ANALYZING after a crash -> INTERRUPTED
    assert store.load(h.path).project.status == ProjectStatus.INTERRUPTED

    (home / "Projecten" / "kapot").mkdir()
    (home / "Projecten" / "kapot" / "project.json").write_text("{niet geldig")
    handles, broken = store.list_projects()
    assert len(handles) == 1 and len(broken) == 1

    store.delete(h)
    assert not h.path.exists()
    with pytest.raises(ProjectError):
        store.delete(Path("/tmp"))


def test_project_roundtrip_dict(synthetic_video):
    info = probe_video(synthetic_video)
    p = Project("id", "n", "", "", info, "video/x.mp4", True)
    assert Project.from_dict(p.to_dict()).to_dict() == p.to_dict()
