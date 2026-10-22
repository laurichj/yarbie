from __future__ import annotations

import asyncio
import ipaddress
import json
from pathlib import Path

import pytest

from yarbie import discovery
from yarbie.discovery import DiscoveryError, Found


class Network:
    """Stands in for the LAN: which hosts carry which robots."""

    def __init__(self, robots: dict[str, list[str]]) -> None:
        self.robots = robots
        self.scans = 0

    async def scan(self, spec: str | None = None, serial: str | None = None) -> list[Found]:
        self.scans += 1
        return [Found(host, serials) for host, serials in self.robots.items()]

    async def confirm(self, host: str, serial: str) -> bool:
        return serial in self.robots.get(host, [])


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Network:
    network = Network({})
    monkeypatch.setattr(discovery, "scan", network.scan)
    monkeypatch.setattr(discovery, "confirm", network.confirm)
    return network


def locate(serial: str | None, cache: Path) -> tuple[str, str]:
    return asyncio.run(discovery.locate(serial, cache))


def test_finds_and_remembers(net: Network, tmp_path: Path) -> None:
    cache = tmp_path / "lan_robot.json"
    net.robots = {"192.168.1.40": ["SN1"]}
    assert locate(None, cache) == ("192.168.1.40", "SN1")
    assert json.loads(cache.read_text()) == {"host": "192.168.1.40", "serial": "SN1"}
    # Next start: the remembered address still answers, so no scan.
    assert locate(None, cache) == ("192.168.1.40", "SN1")
    assert net.scans == 1


def test_rescans_when_the_address_changed(net: Network, tmp_path: Path) -> None:
    cache = tmp_path / "lan_robot.json"
    cache.write_text(json.dumps({"host": "192.168.1.40", "serial": "SN1"}))
    net.robots = {"192.168.1.77": ["SN1"]}
    assert locate(None, cache) == ("192.168.1.77", "SN1")
    assert json.loads(cache.read_text())["host"] == "192.168.1.77"


def test_several_robots_need_a_serial(net: Network, tmp_path: Path) -> None:
    net.robots = {"192.168.1.40": ["SN1"], "192.168.1.41": ["SN2"]}
    with pytest.raises(DiscoveryError, match="YARBO_SERIAL"):
        locate(None, tmp_path / "c.json")
    assert locate("SN2", tmp_path / "c.json") == ("192.168.1.41", "SN2")


def test_base_station_relay_is_the_same_robot(net: Network, tmp_path: Path) -> None:
    net.robots = {"192.168.1.41": ["SN1"], "192.168.1.40": ["SN1"]}
    assert locate(None, tmp_path / "c.json") == ("192.168.1.40", "SN1")


def test_nothing_found(net: Network, tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError, match="no Yarbo robot found"):
        locate(None, tmp_path / "c.json")


def test_local_subnets_are_private_slash_24s() -> None:
    for subnet in discovery.local_subnets():
        net = ipaddress.ip_network(subnet)
        assert net.prefixlen == 24 and net.is_private
