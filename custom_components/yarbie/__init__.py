"""yarbie: a Yarbo robot over its own LAN broker, with a map editor panel."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import discovery, panel, services, websocket
from ._vendor.yarbo_local.exceptions import YarboError
from ._vendor.yarbo_local.registry import Registry
from .const import CONF_SCAN, CONF_SERIAL, CONF_SIMULATOR, DOMAIN
from .coordinator import YarbieConfigEntry, YarbieCoordinator
from .robot import Robot

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.LAWN_MOWER,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket.async_register(hass)
    services.async_register(hass)
    await panel.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YarbieConfigEntry) -> bool:
    # The command registry is a YAML file parsed once per process: not on the event loop.
    await hass.async_add_executor_job(Registry.default)
    if entry.data.get(CONF_SIMULATOR):
        robot = Robot.simulated()
    else:
        host = await _current_host(hass, entry)
        robot = Robot.lan(host, entry.data.get(CONF_SERIAL))
    coordinator = YarbieCoordinator(hass, entry, robot)
    try:
        await coordinator.async_start()
    except (YarboError, TimeoutError, OSError) as err:
        await robot.close()
        raise ConfigEntryNotReady(f"cannot reach the robot: {err}") from err
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YarbieConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _current_host(hass: HomeAssistant, entry: YarbieConfigEntry) -> str:
    """The stored address if the robot still answers there, else a scan for its serial."""
    host: str | None = entry.data.get(CONF_HOST)
    serial: str | None = entry.data.get(CONF_SERIAL)
    if host and (serial is None or await discovery.confirm(host, serial)):
        return host
    if serial is None:
        raise ConfigEntryNotReady("no address stored for the robot")
    spec = entry.options.get(CONF_SCAN) or ",".join(await discovery.local_subnets(hass))
    if not spec:
        raise ConfigEntryNotReady("no network to scan for the robot; set a subnet in the options")
    found = [f for f in await discovery.scan(spec, serial) if serial in f.serials]
    if not found:
        raise ConfigEntryNotReady(f"robot {serial} not found at {host} or on {spec}")
    new_host = sorted(f.host for f in found)[0]
    _LOGGER.info("robot %s moved from %s to %s", serial, host, new_host)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: new_host})
    return new_host
