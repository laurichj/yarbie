"""The integration under Home Assistant's test harness, against the built-in simulator."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie.const import CONF_SERIAL, CONF_SIMULATOR, DOMAIN
from custom_components.yarbie.editor import demo
from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Every test here loads custom_components/yarbie."""


@pytest.fixture(autouse=True)
def own_config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Backups go under the config dir; keep each test's apart (the harness shares one)."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture
def options() -> dict[str, Any]:
    return {}


@pytest.fixture
async def entry(hass: HomeAssistant, options: dict[str, Any]) -> AsyncIterator[MockConfigEntry]:
    assert await async_setup_component(hass, "repairs", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=demo.SERIAL,
        title="Yarbo (simulator)",
        data={CONF_SIMULATOR: True, CONF_SERIAL: demo.SERIAL},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


class WS:
    """Sends one editor command and returns the reply message; ``client`` for the rest."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def __call__(self, type_: str, **data: Any) -> dict[str, Any]:
        await self.client.send_json_auto_id({"type": type_, **data})
        return await self.client.receive_json()


@pytest.fixture
async def ws(hass: HomeAssistant, hass_ws_client: Any, entry: MockConfigEntry) -> WS:
    return WS(await hass_ws_client(hass))


def state(hass: HomeAssistant, entity_id: str) -> State:
    got = hass.states.get(entity_id)
    assert got is not None, f"{entity_id} does not exist"
    return got


def square(lon: float, lat: float, d: float = 0.00002) -> list[list[float]]:
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
