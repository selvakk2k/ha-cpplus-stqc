"""Switch platform for CP PLUS STQC integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, TYPE_NVR
from .coordinator import CPPlusDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS switch entities."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = []

    if coordinator.client.device_type == TYPE_NVR and coordinator.channels:
        for ch in coordinator.channels:
            ch_idx = ch["index"]
            ch_num = ch["channel"]
            ch_name = ch["name"]

            # Human Detection arming switch
            entities.append(
                CPPlusDetectionSwitch(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                    feature_key="smd_human",
                    name="Human Detection Arming",
                    icon="mdi:account-search",
                )
            )

            # Vehicle Detection arming switch
            entities.append(
                CPPlusDetectionSwitch(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                    feature_key="smd_vehicle",
                    name="Vehicle Detection Arming",
                    icon="mdi:car-search",
                )
            )

            # Tripwire arming switch
            entities.append(
                CPPlusDetectionSwitch(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                    feature_key="tripwire",
                    name="Tripwire Arming",
                    icon="mdi:ray-start-end",
                )
            )

            # Audio stream transmission switch
            entities.append(
                CPPlusDetectionSwitch(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                    feature_key="audio_enable",
                    name="Audio Stream",
                    icon="mdi:microphone",
                )
            )

    async_add_entities(entities)


class CPPlusDetectionSwitch(CoordinatorEntity[CPPlusDataUpdateCoordinator], SwitchEntity):
    """Switch entity to toggle AI detection and tripwire algorithms per channel."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        channel_idx: int,
        channel_num: int,
        channel_name: str,
        feature_key: str,
        name: str,
        icon: str,
    ) -> None:
        """Initialize detection switch entity."""
        super().__init__(coordinator)
        self._channel_idx = channel_idx
        self._channel_num = channel_num
        self._channel_name = channel_name
        self._feature_key = feature_key

        nvr_serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{nvr_serial}_ch{channel_num}_{feature_key}_switch"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = self.coordinator.get_channel_device_info(channel_num, channel_name)

    @property
    def is_on(self) -> bool:
        """Return true if feature is enabled."""
        if not self.coordinator.channels:
            return False
        ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
        if not ch:
            return False
        return bool(ch.get(self._feature_key, False))

    @property
    def icon(self) -> str | None:
        """Return icon based on state."""
        if self._feature_key == "audio_enable":
            return "mdi:microphone" if self.is_on else "mdi:microphone-off"
        return self._attr_icon

    @property
    def available(self) -> bool:
        """Return true if NVR is online."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the feature on."""
        if self._feature_key == "smd_human":
            success = await self.coordinator.client.async_set_smd_human(self._channel_idx, True)
        elif self._feature_key == "smd_vehicle":
            success = await self.coordinator.client.async_set_smd_vehicle(self._channel_idx, True)
        elif self._feature_key == "tripwire":
            success = await self.coordinator.client.async_set_tripwire(self._channel_idx, True)
        elif self._feature_key == "audio_enable":
            success = await self.coordinator.client.async_set_audio_enable(self._channel_idx, True)
        else:
            success = False

        if success:
            ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
            if ch:
                ch[self._feature_key] = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the feature off."""
        if self._feature_key == "smd_human":
            success = await self.coordinator.client.async_set_smd_human(self._channel_idx, False)
        elif self._feature_key == "smd_vehicle":
            success = await self.coordinator.client.async_set_smd_vehicle(self._channel_idx, False)
        elif self._feature_key == "tripwire":
            success = await self.coordinator.client.async_set_tripwire(self._channel_idx, False)
        elif self._feature_key == "audio_enable":
            success = await self.coordinator.client.async_set_audio_enable(self._channel_idx, False)
        else:
            success = False

        if success:
            ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
            if ch:
                ch[self._feature_key] = False
            self.async_write_ha_state()
