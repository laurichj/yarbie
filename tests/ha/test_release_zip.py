"""scripts/build_release_zip.py, which builds the zip HACS installs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from custom_components.yarbie import panel

SCRIPT = Path(__file__).parents[2] / "scripts" / "build_release_zip.py"


def test_the_zip_requires_what_the_panel_loads() -> None:
    # The script cannot import panel.py (it would need Home Assistant), so it keeps its
    # own list; a release missing one of these installs without its UI.
    spec = importlib.util.spec_from_file_location("build_release_zip", SCRIPT)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.BUILT == (panel.MODULE, panel.DASHBOARD, panel.CARDS)
