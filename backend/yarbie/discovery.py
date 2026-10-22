"""Find the robot on the LAN when no host is configured.

A robot is recognised by the serial in its heartbeats on port 1883 (yarbo-local's
scanner), never by MAC or hostname. The address found is remembered and checked again
on the next start, since the router may hand the robot a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket

from yarbo_local import discover

log = logging.getLogger("yarbie.discovery")

PORT = 1883
DOCKER_RANGE = ipaddress.ip_network("172.16.0.0/12")
# Heartbeats come every 2 s awake and 5 s asleep; hearing one confirms a known address.
CONFIRM_WINDOW = 6.0


@dataclass(frozen=True, slots=True)
class Found:
    host: str
    serials: list[str]


class DiscoveryError(RuntimeError):
    pass


def local_subnets() -> list[str]:
    """The /24 around the address this machine uses to reach the network.

    The netmask is not available without extra dependencies; /24 is what home routers
    hand out. Set ``YARBIE_SCAN`` to scan something else.
    """
    addresses: set[str] = set()
    try:
        # Connecting a UDP socket sends nothing; it only picks the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    subnets: list[str] = []
    for address in sorted(addresses):
        ip = ipaddress.ip_address(address)
        if not ip.is_private or ip.is_loopback or ip.is_link_local:
            continue
        # Inside a container this is Docker's own bridge, not the LAN.
        if in_container() and ip in DOCKER_RANGE:
            continue
        net = str(ipaddress.ip_network(f"{address}/24", strict=False))
        if net not in subnets:
            subnets.append(net)
    return subnets


def in_container() -> bool:
    return Path("/.dockerenv").exists()


async def scan(spec: str | None = None, serial: str | None = None) -> list[Found]:
    """Every broker carrying robot heartbeats on ``spec`` (CIDRs/IPs) or the local subnets."""
    spec = spec or os.environ.get("YARBIE_SCAN") or ",".join(local_subnets())
    if not spec:
        raise DiscoveryError(
            "cannot see the LAN from inside Docker; set YARBIE_SCAN (e.g. 192.168.1.0/24) "
            "or YARBO_HOST"
            if in_container()
            else "no private network address found to scan; set YARBIE_SCAN"
        )
    log.info("scanning %s for Yarbo robots on port %d", spec, PORT)
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


async def locate(serial: str | None, cache: Path) -> tuple[str, str]:
    """``(host, serial)`` of the robot: the remembered address if it still answers,
    else a scan. Raises :class:`DiscoveryError` when there is none, or more than one
    robot and no serial to choose by."""
    remembered = _read(cache)
    if remembered and (serial is None or serial == remembered[1]):
        host, known = remembered
        if await confirm(host, known):
            log.info("robot %s still at %s", known, host)
            return host, known
        log.info("robot %s no longer at %s; scanning", known, host)

    found = await scan(serial=serial)
    if serial is not None:
        found = [f for f in found if serial in f.serials]
    serials = sorted({s for f in found for s in f.serials})
    if not found:
        hint = (
            " Inside Docker, set YARBIE_SCAN to your LAN (e.g. 192.168.1.0/24) or YARBO_HOST."
            if in_container()
            else " Set YARBO_HOST, or YARBIE_SCAN if the robot is on another subnet."
        )
        wanted = f"robot {serial}" if serial else "Yarbo robot"
        raise DiscoveryError(f"no {wanted} found on the network.{hint}")
    if serial is None and len(serials) > 1:
        raise DiscoveryError(
            f"found several robots ({', '.join(serials)}); set YARBO_SERIAL to choose one"
        )
    chosen_serial = serial or serials[0]
    # A base station may relay the same robot from a second address; any works, the
    # rest are logged so the user can pin one with YARBO_HOST.
    hosts = sorted(f.host for f in found if chosen_serial in f.serials)
    if len(hosts) > 1:
        log.info("robot %s heard at %s; using %s", chosen_serial, ", ".join(hosts), hosts[0])
    _write(cache, hosts[0], chosen_serial)
    return hosts[0], chosen_serial


def _read(cache: Path) -> tuple[str, str] | None:
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return str(data["host"]), str(data["serial"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(cache: Path, host: str, serial: str) -> None:
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"host": host, "serial": serial}), encoding="utf-8")
    except OSError as err:
        log.warning("could not remember the robot's address: %s", err)
