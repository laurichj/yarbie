"""The Yard editor: a sidebar panel served from this integration's ``www/``."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

WWW = Path(__file__).parent / "www"
MODULE = "yarbie-panel.js"
URL_BASE = f"/{DOMAIN}_static"
PANEL_URL = "yard-editor"


async def async_register(hass: HomeAssistant) -> None:
    if "frontend" not in hass.config.components:
        return  # no UI to add a panel to (as in tests)
    module = WWW / MODULE
    if not await hass.async_add_executor_job(module.is_file):
        _LOGGER.warning("the Yard editor is not built (%s missing); the panel is not shown", module)
        return
    digest = await hass.async_add_executor_job(_digest, module)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(WWW), cache_headers=True)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="yarbie-panel",
        sidebar_title="Yard editor",
        sidebar_icon="mdi:map-marker-path",
        # The hash makes a rebuilt panel load at once despite the long cache headers.
        module_url=f"{URL_BASE}/{MODULE}?v={digest}",
        require_admin=True,
        # Its own document: the editor's styles and element lookups stay as they were.
        embed_iframe=True,
        config={},
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
