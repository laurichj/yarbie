"""Find the robot on the LAN.

A robot is recognised by the serial in its heartbeats on port 1883 (yarbo-local's
scanner), never by MAC or hostname. The address is kept in the config entry and checked
again at start-up, since the router may hand the robot a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging

from homeassistant.components import network
from homeassistant.core import HomeAssistant

from ._vendor.yarbo_local import discover

_LOGGER = logging.getLogger(__name__)

PORT = 1883
# Heartbeats come every 2 s awake and 5 s asleep; hearing one confirms a known address.
CONFIRM_WINDOW = 6.0


@dataclass(frozen=True, slots=True)
class Found:
    host: str
    serials: list[str]


async def local_subnets(hass: HomeAssistant) -> list[str]:
    """The IPv4 networks of the adapters HA uses, capped at /24 (home routers hand out
    /24; scanning more would take minutes)."""
    out: list[str] = []
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ip in adapter["ipv4"]:
            address = ipaddress.ip_address(ip["address"])
            if not address.is_private or address.is_loopback or address.is_link_local:
                continue
            prefix = max(int(ip["network_prefix"]), 24)
            net = str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))
            if net not in out:
                out.append(net)
    return out


async def scan(spec: str, serial: str | None = None) -> list[Found]:
    """Every broker carrying robot heartbeats on ``spec`` (comma-separated CIDRs or IPs)."""
    _LOGGER.info("scanning %s for Yarbo robots on port %d", spec, PORT)
    hits = await discover.discover(
        discover.expand(spec),
        port=PORT,
        # Generous: a robot on Wi-Fi power saving can be slow to accept a connection.
        connect_timeout=1.5,
        concurrency=64,
        heartbeat_only=True,
        names=False,
        stop_on=serial,
    )
    return [Found(hit.host, hit.serials) for hit in hits if hit.serials]


async def confirm(host: str, serial: str) -> bool:
    """Whether ``host`` still carries ``serial``."""
    if not await discover.tcp_open(host, PORT, 1.5):
        return False
    heard = await discover.sample(host, PORT, wait=CONFIRM_WINDOW, stop_on=serial)
    return serial in heard.serials


async def identify(host: str) -> list[str]:
    """The serials heard on ``host``; empty when nothing answers there."""
    if not await discover.tcp_open(host, PORT, 3.0):
        return []
    heard = await discover.sample(host, PORT, wait=CONFIRM_WINDOW)
    return sorted(heard.serials)
