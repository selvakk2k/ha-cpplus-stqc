"""Button platform for CP PLUS STQC integration."""

from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity, ButtonDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CPPlusDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS button entities."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = [
        CPPlusRebootButton(coordinator),
    ]

    async_add_entities(entities)


class CPPlusRebootButton(CoordinatorEntity[CPPlusDataUpdateCoordinator], ButtonEntity):
    """Button to reboot CP PLUS camera."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator: CPPlusDataUpdateCoordinator) -> None:
        """Initialize reboot button."""
        super().__init__(coordinator)
        serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{serial}_reboot"
        self._attr_name = "Reboot"
        self._attr_device_info = self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Return true if entity is available."""
        return bool(self.coordinator.data and self.coordinator.data.get("online", False))

    async def async_press(self) -> None:
        """Handle button press."""
        _LOGGER.info("Rebooting CP PLUS camera %s", self.coordinator.client.host)
        await self.coordinator.client.async_reboot()
