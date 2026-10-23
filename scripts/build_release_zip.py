"""Zip the integration as HACS installs it from a release: ``yarbie.zip``.

    (cd frontend && npm ci && npm run build)
    python3 scripts/build_release_zip.py [--tag vX.Y.Z] [--out yarbie.zip]
    python3 scripts/build_release_zip.py --tag vX.Y.Z --check-version  # no zip

HACS (``zip_release`` in hacs.json) extracts the zip straight into
``custom_components/yarbie``, so the integration's files sit at the zip's root. The zip
holds the files git tracks there plus the built ``www/``, which git does not; never
``__pycache__``, tests or symlinks. Fails unless ``manifest.json`` and ``pyproject.toml``
have the same version, and with ``--tag``, unless the tag is ``v`` + that version.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "yarbie"
WWW = INTEGRATION / "www"
# What panel.py loads (MODULE, DASHBOARD, CARDS; a test keeps them equal); without them
# the release would install without its UI.
BUILT = ("yarbie-panel.js", "yarbie-dashboard.js", "yarbie-cards.js")
# Zip entries get a fixed date, so the same tree always gives the same zip.
EPOCH = (1980, 1, 1, 0, 0, 0)


def wanted(path: Path) -> bool:
    rel = path.relative_to(INTEGRATION)
    if path.is_symlink() or not path.is_file():
        return False
    if "__pycache__" in rel.parts or path.suffix == ".pyc":
        return False
    return not any(part in ("tests", "test") or part.startswith("test_") for part in rel.parts)


def files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", str(INTEGRATION.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    paths = {ROOT / t for t in tracked if t}
    paths.update(WWW.rglob("*"))
    return sorted(p for p in paths if wanted(p))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="the release tag; must be v + manifest.json's version")
    parser.add_argument("--out", type=Path, default=ROOT / "yarbie.zip")
    parser.add_argument("--check-version", action="store_true", help="check the versions only")
    args = parser.parse_args()

    version = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))["version"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if project != version:
        print(f"pyproject.toml's version {project} differs from manifest.json's {version}", file=sys.stderr)
        return 1
    if args.tag is not None and args.tag != f"v{version}":
        print(f"tag {args.tag} does not match manifest.json's version {version} (want v{version})", file=sys.stderr)
        return 1
    if args.check_version:
        print(f"version {version} ok")
        return 0
    missing = [name for name in BUILT if not (WWW / name).is_file()]
    if missing:
        print(f"frontend not built ({', '.join(missing)} missing); run npm run build in frontend/", file=sys.stderr)
        return 1

    with zipfile.ZipFile(args.out, "w") as zf:
        for path in files():
            info = zipfile.ZipInfo(str(path.relative_to(INTEGRATION)), EPOCH)
            info.create_system = 3  # Unix, on every platform, for the same bytes everywhere
            info.external_attr = 0o100644 << 16  # a regular file, rw-r--r--
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compresslevel=9)
        count = len(zf.namelist())
    print(f"wrote {args.out} (yarbie {version}, {count} files, {args.out.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
