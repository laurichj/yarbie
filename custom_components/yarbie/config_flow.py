"""Adding a robot: discovered by DHCP, typed in, or found by scanning the LAN."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import discovery
from .const import (
    CONF_SCAN,
    CONF_SERIAL,
    CONF_SIMULATOR,
    DOMAIN,
    OPT_ALLOW_WRITES,
    OPT_GOOGLE_MAPS_KEY,
    OPT_KEEP_AWAKE,
    OPT_OBSERVE_ONLY,
    OPT_RUN_RETENTION_DAYS,
    OPT_SMOOTHING_TOLERANCE,
    OPT_SMOOTHING_TOLERANCE_M,
    SIMULATOR_HOST,
    SMOOTHING_LIMITS,
)
from .editor import demo, smooth
from .runlog import DEFAULT_RETENTION_DAYS
from .units import short_length


class YarbieConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, str] = {}  # serial -> host
        self._host: str | None = None
        self._serial: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> YarbieOptionsFlow:
        return YarbieOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = (user_input.get(CONF_HOST) or "").strip()
            if host == SIMULATOR_HOST:
                await self.async_set_unique_id(demo.SERIAL)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Yarbo (simulator)", data={CONF_SIMULATOR: True, CONF_SERIAL: demo.SERIAL}
                )
            if host:
                serials = await discovery.identify(host)
                if not serials:
                    errors[CONF_HOST] = "cannot_connect"
                else:
                    self._found = {s: host for s in serials}
            else:
                spec = (user_input.get(CONF_SCAN) or "").strip() or ",".join(
                    await discovery.local_subnets(self.hass)
                )
                if not spec:
                    errors["base"] = "no_network"
                else:
                    for hit in await discovery.scan(spec):
                        for serial in hit.serials:
                            self._found.setdefault(serial, hit.host)
                    if not self._found:
                        errors["base"] = "not_found"
            if not errors:
                if len(self._found) == 1:
                    serial, found_host = next(iter(self._found.items()))
                    return await self._create(serial, found_host)
                return await self.async_step_pick()
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {probatio.Optional(CONF_HOST, default=""): str, probatio.Optional(CONF_SCAN, default=""): str}
            ),
            errors=errors,
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            serial = user_input[CONF_SERIAL]
            return await self._create(serial, self._found[serial])
        return self.async_show_form(
            step_id="pick",
            data_schema=probatio.Schema({probatio.Required(CONF_SERIAL): probatio.In(sorted(self._found))}),
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        serials = await discovery.identify(discovery_info.ip)
        if not serials:
            return self.async_abort(reason="not_a_robot")
        self._serial, self._host = serials[0], discovery_info.ip
        await self.async_set_unique_id(self._serial)
        # A known robot with a new address: follow it.
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})
        self.context["title_placeholders"] = {"serial": self._serial}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._serial is not None and self._host is not None
        if user_input is not None:
            return await self._create(self._serial, self._host)
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"serial": self._serial, "host": self._host},
        )

    async def _create(self, serial: str, host: str) -> ConfigFlowResult:
        await self.async_set_unique_id(serial)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(
            title=f"Yarbo {serial}", data={CONF_HOST: host, CONF_SERIAL: serial}
        )


class YarbieOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        unit, unit_m = short_length(self.hass)
        tolerance_key = OPT_SMOOTHING_TOLERANCE[unit]
        if user_input is not None:
            data = dict(user_input)
            if tolerance_key in data:
                data[OPT_SMOOTHING_TOLERANCE_M] = round(float(data.pop(tolerance_key)) * unit_m, 4)
            return self.async_create_entry(data=data)
        options = self.config_entry.options
        tolerance = float(options.get(OPT_SMOOTHING_TOLERANCE_M, smooth.DEFAULT_TOLERANCE_M))
        low, high = SMOOTHING_LIMITS[unit]
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(
                        OPT_ALLOW_WRITES, default=options.get(OPT_ALLOW_WRITES, True)
                    ): bool,
                    probatio.Optional(
                        OPT_OBSERVE_ONLY, default=options.get(OPT_OBSERVE_ONLY, False)
                    ): bool,
                    probatio.Optional(OPT_KEEP_AWAKE, default=options.get(OPT_KEEP_AWAKE, False)): bool,
                    probatio.Optional(CONF_SCAN, default=options.get(CONF_SCAN, "")): str,
                    probatio.Optional(
                        OPT_GOOGLE_MAPS_KEY, default=options.get(OPT_GOOGLE_MAPS_KEY, "")
                    ): str,
                    probatio.Optional(
                        OPT_RUN_RETENTION_DAYS,
                        default=options.get(OPT_RUN_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
                    ): probatio.All(probatio.Coerce(int), probatio.Range(min=7, max=3650)),
                    probatio.Optional(tolerance_key, default=round(tolerance / unit_m, 1)): probatio.All(
                        probatio.Coerce(float), probatio.Range(min=low, max=high)
                    ),
                }
            ),
        )
