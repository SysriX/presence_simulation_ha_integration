"""sensor.haus_sys_presence_sim: off | ready | active; Attribute = Einstellungen, Profile, Tagesplan, Protokoll."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENT_STATUS, SIGNAL_UPDATE
from .manager import SimManager


async def async_setup_platform(hass: HomeAssistant, _config, async_add_entities: AddEntitiesCallback, _discovery=None) -> None:
    async_add_entities([StatusSensor(hass.data[DOMAIN])])


class StatusSensor(SensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:home-lightbulb-outline"
    _attr_name = "Haus Anwesenheitssimulation"
    _attr_unique_id = f"{DOMAIN}_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["off", "ready", "active"]
    _attr_translation_key = "status"
    _unrecorded_attributes = frozenset({"settings", "profiles", "rooms", "plan", "next", "running_lamps", "log",
                                        "version", "presence_source", "away_since", "since", "reason"})

    def __init__(self, mgr: SimManager) -> None:
        self.mgr = mgr
        self.entity_id = ENT_STATUS

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self.mgr.status()

    @property
    def extra_state_attributes(self) -> dict:
        return self.mgr.view()
