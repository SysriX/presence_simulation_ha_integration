"""Anwesenheitssimulation (presence_sim) – für die ganze Wohnung, bearbeitet im Raum-Panel
(Einstellungen → Allgemein → Anwesenheit). Einstellungen, Profile und Tagesplan liegen in .storage/presence_sim.

configuration.yaml:

    presence_sim:
      rooms: [wz, bu, ku, sz, fl, ke, ba, bz]   # optional, das ist der Standard
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import CONF_ROOMS, DEFAULT_ROOMS, DOMAIN, GLOBAL_MODES
from .manager import SimManager

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Any(None, vol.Schema({
    vol.Optional(CONF_ROOMS, default=DEFAULT_ROOMS): vol.All(cv.ensure_list, [cv.slug]),
}))}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN) or {}
    mgr = SimManager(hass, conf.get(CONF_ROOMS) or DEFAULT_ROOMS)
    hass.data[DOMAIN] = mgr
    websocket.async_register(hass)
    await mgr.async_start()

    async def _guard(coro) -> None:
        try:
            await coro
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def set_mode(call: ServiceCall) -> None:
        await _guard(mgr.async_set_mode(call.data["mode"]))

    async def start(call: ServiceCall) -> None:
        await _guard(mgr.async_start_manual(call.data.get("profile_id")))

    async def stop(_call: ServiceCall) -> None:
        await _guard(mgr.async_stop_manual())

    async def replan(_call: ServiceCall) -> None:
        await _guard(mgr.async_replan())

    hass.services.async_register(DOMAIN, "set_mode", set_mode, vol.Schema({vol.Required("mode"): vol.In(GLOBAL_MODES)}))
    hass.services.async_register(DOMAIN, "start", start, vol.Schema({vol.Optional("profile_id"): cv.string}))
    hass.services.async_register(DOMAIN, "stop", stop, vol.Schema({}))
    hass.services.async_register(DOMAIN, "replan", replan, vol.Schema({}))
    for platform in (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT):
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))
    return True
