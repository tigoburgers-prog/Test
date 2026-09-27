"""Download and verify the bundled detection models (run once before building)."""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from traffic_snapshot.analysis.detection import MODEL_SPECS  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(keys: list[str]) -> int:
    target = ROOT / "models"
    target.mkdir(exist_ok=True)
    for key in keys or list(MODEL_SPECS):
        spec = MODEL_SPECS[key]
        path = target / spec.file
        if path.is_file() and sha256(path) == spec.sha256:
            print(f"✓ {spec.file} (al aanwezig)")
            continue
        print(f"↓ {spec.file} ophalen …")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(spec.url, tmp)
        digest = sha256(tmp)
        if digest != spec.sha256:
            tmp.unlink()
            print(f"✗ Hash klopt niet voor {spec.file}: {digest}")
            return 1
        tmp.replace(path)
        print(f"✓ {spec.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
