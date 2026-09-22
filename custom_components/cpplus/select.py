"""Select platform for CP PLUS STQC integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DOMAIN,
    SUBENTRY_TYPE_CHANNEL,
    TYPE_NVR,
    DAY_NIGHT_MODES,
    DAY_NIGHT_NAME_TO_INT,
    LIGHTING_MODES,
)
from .coordinator import CPPlusDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS select entities."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    if coordinator.client.device_type == TYPE_NVR and coordinator.channels:
        subentries = {
            int(s.data["channel"]): s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            if "channel" in s.data
        }
        for ch in coordinator.channels:
            # Only create control selectors for native CP PLUS cameras (CP-*)
            if not ch.get("is_native_cpplus", False):
                continue

            ch_idx = ch["index"]
            ch_num = ch["channel"]
            subentry = subentries.get(ch_num)
            ch_name = (subentry.title if subentry and subentry.title else None) or ch["name"]
            subentry_id = subentry.subentry_id if subentry else None

            channel_entities = [
                CPPlusDayNightSelect(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                ),
                CPPlusIlluminatorSelect(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                ),
            ]
            async_add_entities(channel_entities, config_subentry_id=subentry_id)


class CPPlusDayNightSelect(CoordinatorEntity[CPPlusDataUpdateCoordinator], SelectEntity):
    """Select entity to control Day/Night mode (Color, Auto, Black & White)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:theme-light-dark"
    _attr_options = list(DAY_NIGHT_MODES.values())

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        channel_idx: int,
        channel_num: int,
        channel_name: str,
    ) -> None:
        """Initialize Day/Night select entity."""
        super().__init__(coordinator)
        self._channel_idx = channel_idx
        self._channel_num = channel_num
        self._channel_name = channel_name

        nvr_serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{nvr_serial}_ch{channel_num}_day_night"
        self._attr_name = "Day/Night Mode"
        self._attr_device_info = self.coordinator.get_channel_device_info(channel_num, channel_name)

    @property
    def current_option(self) -> str | None:
        """Return the current Day/Night option."""
        if not self.coordinator.channels:
            return None
        ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
        if not ch:
            return None
        mode_val = ch.get("video_in_mode", 1)
        return DAY_NIGHT_MODES.get(mode_val, "Auto")

    @property
    def available(self) -> bool:
        """Return true if NVR is online."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

    async def async_select_option(self, option: str) -> None:
        """Change Day/Night option."""
        mode_int = DAY_NIGHT_NAME_TO_INT.get(option, 1)
        success = await self.coordinator.client.async_set_video_in_mode(self._channel_idx, mode_int)
        if success:
            ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
            if ch:
                ch["video_in_mode"] = mode_int
            self.async_write_ha_state()


class CPPlusIlluminatorSelect(CoordinatorEntity[CPPlusDataUpdateCoordinator], SelectEntity):
    """Select entity to control Camera Illuminator (Auto, Manual, Off)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lightbulb-auto"
    _attr_options = LIGHTING_MODES

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        channel_idx: int,
        channel_num: int,
        channel_name: str,
    ) -> None:
        """Initialize Illuminator select entity."""
        super().__init__(coordinator)
        self._channel_idx = channel_idx
        self._channel_num = channel_num
        self._channel_name = channel_name

        nvr_serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{nvr_serial}_ch{channel_num}_illuminator"
        self._attr_name = "Illuminator Mode"
        self._attr_device_info = self.coordinator.get_channel_device_info(channel_num, channel_name)

    @property
    def current_option(self) -> str | None:
        """Return the current Illuminator option."""
        if not self.coordinator.channels:
            return None
        ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
        if not ch:
            return None
        return ch.get("lighting_mode", "Auto")

    @property
    def available(self) -> bool:
        """Return true if NVR is online."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

    async def async_select_option(self, option: str) -> None:
        """Change Illuminator option."""
        success = await self.coordinator.client.async_set_lighting_mode(self._channel_idx, option)
        if success:
            ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
            if ch:
                ch["lighting_mode"] = option
            self.async_write_ha_state()
