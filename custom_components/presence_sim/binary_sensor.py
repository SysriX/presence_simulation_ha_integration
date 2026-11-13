"""binary_sensor.haus_sys_presence_sim_active: Simulation läuft. Attribute lock_motion / lock_schedule / lock_lux
sind die Sperre für Bewegungs-, Zeit- und Helligkeitssteuerung."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ENT_ACTIVE, SIGNAL_UPDATE
from .manager import SimManager


async def async_setup_platform(hass: HomeAssistant, _config, async_add_entities: AddEntitiesCallback, _discovery=None) -> None:
    async_add_entities([ActiveSensor(hass.data[DOMAIN])])


class ActiveSensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:home-lightbulb"
    _attr_name = "Haus Anwesenheitssimulation aktiv"
    _attr_unique_id = f"{DOMAIN}_active"

    def __init__(self, mgr: SimManager) -> None:
        self.mgr = mgr
        self.entity_id = ENT_ACTIVE

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.mgr.active

    @property
    def extra_state_attributes(self) -> dict:
        p = self.mgr.profiles.get(self.mgr.run.get("profile") or "") if self.mgr.active else None
        return {"profile": p["name"] if p else None, **self.mgr.locks()}
