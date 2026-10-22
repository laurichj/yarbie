"""Copy yarbo-local from its subtree into the integration, or check the copy is current.

The subtree at ``third_party/yarbo-local`` is the whole upstream repository, pulled with
``git subtree pull``. Home Assistant only installs ``custom_components/yarbie``, so the
package and its protocol data are copied in beside it, laid out as an installed wheel
would be (``data/*.yaml`` inside the package). The copy is never edited by hand.

    python3 scripts/vendor_yarbo_local.py          # refresh the copy
    python3 scripts/vendor_yarbo_local.py --check  # fail if the copy is stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "third_party" / "yarbo-local"
TARGET = ROOT / "custom_components" / "yarbie" / "_vendor" / "yarbo_local"
DATA = ("commands.yaml", "codes.yaml", "fields.yaml")
# Tools that need extra dependencies or only make sense as a CLI.
SKIP = {"studio", "cli.py", "__pycache__"}

# The one absolute package reference: the installed data directory is looked up by
# name, which is not ``yarbo_local`` once vendored.
PATCHES = [
    (
        "registry.py",
        'resources.files("yarbo_local")',
        'resources.files(__package__ or "yarbo_local")',
    ),
]


def build(dest: Path) -> dict[str, object]:
    src = UPSTREAM / "src" / "yarbo_local"
    shutil.copytree(src, dest, ignore=lambda _d, names: [n for n in names if n in SKIP])
    (dest / "data").mkdir()
    for name in DATA:
        shutil.copy2(UPSTREAM / "protocol" / name, dest / "data" / name)
    for rel, old, new in PATCHES:
        path = dest / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise SystemExit(f"vendor patch for {rel} no longer applies: {old!r}")
        path.write_text(text.replace(old, new), encoding="utf-8")
    pyproject = (UPSTREAM / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    manifest = {
        "upstream": "https://github.com/laurichj/yarbo-local",
        "version": version.group(1) if version else None,
        "files": {
            str(p.relative_to(dest)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(dest.rglob("*"))
            if p.is_file()
        },
    }
    (dest / "VENDORED.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the copy is stale")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "yarbo_local"
        build(fresh)
        if args.check:
            try:
                current = json.loads((TARGET / "VENDORED.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                print(f"{TARGET} is missing; run scripts/vendor_yarbo_local.py", file=sys.stderr)
                return 1
            expected = json.loads((fresh / "VENDORED.json").read_text(encoding="utf-8"))
            on_disk = {
                str(p.relative_to(TARGET)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(TARGET.rglob("*"))
                if p.is_file() and p.name != "VENDORED.json" and "__pycache__" not in p.parts
            }
            if current != expected or on_disk != expected["files"]:
                print("vendored yarbo_local is stale or edited; run scripts/vendor_yarbo_local.py", file=sys.stderr)
                return 1
            # Present here is not enough: an ignore rule once kept data/*.yaml out of git,
            # so every clone but this one lacked the command registry.
            tracked = subprocess.run(
                ["git", "ls-files", "--", str(TARGET.relative_to(ROOT))],
                cwd=ROOT, capture_output=True, text=True, check=True,
            ).stdout.split()
            prefix = f"{TARGET.relative_to(ROOT)}/"
            untracked = sorted(set(expected["files"]) - {t.removeprefix(prefix) for t in tracked})
            if untracked:
                print(f"vendored files not in git (check .gitignore): {', '.join(untracked)}", file=sys.stderr)
                return 1
            return 0
        if TARGET.exists():
            shutil.rmtree(TARGET)
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fresh, TARGET)
    (TARGET.parent / "__init__.py").write_text(
        '"""Third-party code copied in by scripts/vendor_yarbo_local.py."""\n', encoding="utf-8"
    )
    print(f"vendored yarbo_local into {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
