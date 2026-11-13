"""WebSocket-API für das Raum-Panel (Einstellungen → Allgemein → Anwesenheit)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register(hass: HomeAssistant) -> None:
    for cmd in (ws_settings, ws_save, ws_delete, ws_preview, ws_replan, ws_start, ws_stop):
        websocket_api.async_register_command(hass, cmd)


async def _run(connection, msg, coro) -> None:
    try:
        res = await coro
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
        return
    connection.send_result(msg["id"], {"result": res})


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/settings", vol.Required("settings"): dict})
@websocket_api.async_response
async def ws_settings(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_save_settings(msg["settings"]))


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/save", vol.Required("profile"): dict})
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_save(msg["profile"]))


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/delete", vol.Required("profile_id"): str})
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_delete(msg["profile_id"]))


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/preview", vol.Required("profile"): dict})
@websocket_api.async_response
async def ws_preview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_preview(msg["profile"]))


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/replan"})
@websocket_api.async_response
async def ws_replan(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_replan())


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/start", vol.Optional("profile_id"): str})
@websocket_api.async_response
async def ws_start(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_start_manual(msg.get("profile_id")))


@websocket_api.websocket_command({vol.Required("type"): "presence_sim/stop"})
@websocket_api.async_response
async def ws_stop(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _run(connection, msg, hass.data[DOMAIN].async_stop_manual())
