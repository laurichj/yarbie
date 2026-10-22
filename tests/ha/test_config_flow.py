"""Adding a robot (typed, scanned, discovered by DHCP) and following it when it moves."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.yarbie import discovery
from custom_components.yarbie.const import CONF_SERIAL, CONF_SIMULATOR, DOMAIN
from custom_components.yarbie.discovery import Found
from homeassistant.config_entries import SOURCE_DHCP, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo


class Network:
    """Stands in for the LAN: which hosts carry which robots."""

    def __init__(self) -> None:
        self.robots: dict[str, list[str]] = {}
        self.scans: list[str] = []

    async def scan(self, spec: str, serial: str | None = None) -> list[Found]:
        self.scans.append(spec)
        return [Found(host, serials) for host, serials in sorted(self.robots.items())]

    async def identify(self, host: str) -> list[str]:
        return sorted(self.robots.get(host, []))

    async def confirm(self, host: str, serial: str) -> bool:
        return serial in self.robots.get(host, [])

    async def local_subnets(self, hass: HomeAssistant) -> list[str]:
        return ["192.168.1.0/24"]


@pytest.fixture
def no_setup(request: pytest.FixtureRequest) -> Iterator[None]:
    """Flows create entries; their setup (a real MQTT connection) is not under test here."""
    if "entry" in request.fixturenames:
        yield
        return
    with patch("custom_components.yarbie.async_setup_entry", return_value=True):
        yield


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch, no_setup: None) -> Network:
    network = Network()
    for name in ("scan", "identify", "confirm", "local_subnets"):
        monkeypatch.setattr(discovery, name, getattr(network, name))
    return network


async def _user(hass: HomeAssistant, **data: str) -> Any:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], data)


async def test_typed_address(hass: HomeAssistant, net: Network) -> None:
    net.robots = {"192.168.1.40": ["SN1"]}
    result = await _user(hass, host="192.168.1.40")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.168.1.40", CONF_SERIAL: "SN1"}
    assert result["result"].unique_id == "SN1"


async def test_nothing_at_the_address(hass: HomeAssistant, net: Network) -> None:
    result = await _user(hass, host="192.168.1.99")
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"host": "cannot_connect"}


async def test_scan_finds_one(hass: HomeAssistant, net: Network) -> None:
    net.robots = {"192.168.1.40": ["SN1"]}
    result = await _user(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_HOST] == "192.168.1.40"
    assert net.scans == ["192.168.1.0/24"]


async def test_base_station_relay_is_the_same_robot(hass: HomeAssistant, net: Network) -> None:
    net.robots = {"192.168.1.41": ["SN1"], "192.168.1.40": ["SN1"]}
    result = await _user(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_HOST] == "192.168.1.40"


async def test_several_robots_ask_which(hass: HomeAssistant, net: Network) -> None:
    net.robots = {"192.168.1.40": ["SN1"], "192.168.1.41": ["SN2"]}
    result = await _user(hass, scan="10.0.0.0/24")
    assert net.scans == ["10.0.0.0/24"]
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_SERIAL: "SN2"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_HOST] == "192.168.1.41"


async def test_nothing_found(hass: HomeAssistant, net: Network) -> None:
    result = await _user(hass)
    assert result["errors"] == {"base": "not_found"}


async def test_simulator(hass: HomeAssistant, net: Network) -> None:
    result = await _user(hass, host="simulator")
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"][CONF_SIMULATOR] is True


async def test_dhcp_follows_a_known_robot(hass: HomeAssistant, net: Network) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="SN1", data={CONF_HOST: "192.168.1.40", CONF_SERIAL: "SN1"})
    entry.add_to_hass(hass)
    net.robots = {"192.168.1.77": ["SN1"]}
    info = DhcpServiceInfo(ip="192.168.1.77", hostname="yarbo", macaddress="c8fe0f123456")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.168.1.77"


async def test_dhcp_new_robot_asks_first(hass: HomeAssistant, net: Network) -> None:
    net.robots = {"192.168.1.77": ["SN9"]}
    info = DhcpServiceInfo(ip="192.168.1.77", hostname="yarbo", macaddress="c8fe0f123456")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["result"].unique_id == "SN9"


async def test_dhcp_other_device(hass: HomeAssistant, net: Network) -> None:
    info = DhcpServiceInfo(ip="192.168.1.5", hostname="printer", macaddress="c8fe0f000000")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_DHCP}, data=info)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "not_a_robot"


async def test_setup_follows_a_robot_that_moved(hass: HomeAssistant, net: Network) -> None:
    from custom_components.yarbie import _current_host

    entry = MockConfigEntry(domain=DOMAIN, unique_id="SN1", data={CONF_HOST: "192.168.1.40", CONF_SERIAL: "SN1"})
    entry.add_to_hass(hass)
    net.robots = {"192.168.1.40": ["SN1"]}
    assert await _current_host(hass, entry) == "192.168.1.40"
    assert net.scans == []  # the stored address still answers: no scan
    net.robots = {"192.168.1.77": ["SN1"]}
    assert await _current_host(hass, entry) == "192.168.1.77"
    assert entry.data[CONF_HOST] == "192.168.1.77"


async def test_options_flow(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"allow_writes": False, "observe_only": False, "keep_awake": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["allow_writes"] is False
    assert not entry.runtime_data.writable
