"""Sensor platform for CP PLUS STQC integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .coordinator import CPPlusDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS sensor entities."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        CPPlusDiagnosticSensor(coordinator, "serial", "Serial Number", "mdi:barcode"),
        CPPlusDiagnosticSensor(coordinator, "firmware", "Firmware Version", "mdi:cellphone-arrow-down"),
        CPPlusDiagnosticSensor(coordinator, "hardware", "Hardware Model", "mdi:chip"),
    ]

    async_add_entities(entities)


class CPPlusDiagnosticSensor(CoordinatorEntity[CPPlusDataUpdateCoordinator], SensorEntity):
    """Diagnostic telemetry sensor for CP PLUS cameras."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        data_key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize diagnostic sensor."""
        super().__init__(coordinator)
        self._data_key = data_key
        serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{serial}_{data_key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return true if entity is available."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))


    @property
    def native_value(self) -> str | None:
        """Return the sensor value."""
        return self.coordinator.data.get(self._data_key) if self.coordinator.data else None
