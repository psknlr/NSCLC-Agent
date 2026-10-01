"""Build the static GitHub Pages site.

    python web/build.py [--out _site]

Copies the web shell (``web/``) and packs the ``nsclc_agent`` package into
``nsclc_agent.zip`` — the exact code the tests exercise, which the browser
runs under Pyodide. ``build.json`` carries the version and a content hash
(cache-busting for the package download).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PACKAGE = ROOT / "nsclc_agent"
_SKIP_DIRS = {"__pycache__"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def pack_package() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE.rglob("*")):
            if path.is_dir() or _SKIP_DIRS & set(path.parts) \
                    or path.suffix in _SKIP_SUFFIXES:
                continue
            info = zipfile.ZipInfo(str(path.relative_to(ROOT)),
                                   date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def build(out: Path) -> dict:
    sys.path.insert(0, str(ROOT))
    from nsclc_agent import __version__

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out, ignore=shutil.ignore_patterns(
        "build.py", "__pycache__", "*.pyc"))
    package = pack_package()
    (out / "nsclc_agent.zip").write_bytes(package)
    meta = {
        "version": __version__,
        "hash": hashlib.sha256(package).hexdigest()[:16],
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "package_bytes": len(package),
    }
    (out / "build.json").write_text(json.dumps(meta, indent=2),
                                    encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "_site"))
    args = parser.parse_args()
    meta = build(Path(args.out))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
