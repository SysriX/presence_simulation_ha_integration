"""select.haus_sys_presence_sim_mode: off | auto | manual (hausweiter Modus der Anwesenheitssimulation)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENT_MODE, GLOBAL_MODES, SIGNAL_UPDATE
from .manager import SimManager


async def async_setup_platform(hass: HomeAssistant, _config, async_add_entities: AddEntitiesCallback, _discovery=None) -> None:
    async_add_entities([ModeSelect(hass.data[DOMAIN])])


class ModeSelect(SelectEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:home-switch-outline"
    _attr_name = "Haus Anwesenheitssimulation Modus"
    _attr_unique_id = f"{DOMAIN}_mode"
    _attr_options = GLOBAL_MODES
    _attr_translation_key = "mode"

    def __init__(self, mgr: SimManager) -> None:
        self.mgr = mgr
        self.entity_id = ENT_MODE

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        return self.mgr.settings["mode"]

    async def async_select_option(self, option: str) -> None:
        await self.mgr.async_set_mode(option)
