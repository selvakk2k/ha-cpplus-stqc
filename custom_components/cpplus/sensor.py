"""Sensor platform for CP PLUS STQC integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN, SUBENTRY_TYPE_CHANNEL, TYPE_NVR
from .coordinator import CPPlusDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS sensor entities."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Root diagnostic sensors for NVR / Standalone device
    root_entities: list[SensorEntity] = [
        CPPlusDiagnosticSensor(coordinator, "serial", "Serial Number", "mdi:barcode"),
        CPPlusDiagnosticSensor(coordinator, "firmware", "Firmware Version", "mdi:cellphone-arrow-down"),
        CPPlusDiagnosticSensor(coordinator, "hardware", "Hardware Model", "mdi:chip"),
    ]
    async_add_entities(root_entities)

    if coordinator.client.device_type == TYPE_NVR and coordinator.channels:
        subentries = {
            s.data.get("channel"): s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        }
        for ch in coordinator.channels:
            # Provide read-only diagnostic status sensors for third-party cameras (where switches/selects are disabled)
            if not ch.get("is_native_cpplus", False):
                ch_idx = ch["index"]
                ch_num = ch["channel"]
                subentry = subentries.get(ch_num)
                ch_name = (subentry.title if subentry and subentry.title else None) or ch["name"]
                subentry_id = subentry.subentry_id if subentry else None

                channel_entities = [
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "audio_enable", "Audio Stream Status", "mdi:microphone"
                    ),
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "video_in_mode", "Day/Night Mode Status", "mdi:theme-light-dark"
                    ),
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "lighting_mode", "Illuminator Mode Status", "mdi:lightbulb-outline"
                    ),
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "smd_human", "Human Detection Arming Status", "mdi:account-search"
                    ),
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "smd_vehicle", "Vehicle Detection Arming Status", "mdi:car-search"
                    ),
                    CPPlusChannelStatusSensor(
                        coordinator, ch_idx, ch_num, ch_name, "tripwire", "Tripwire Arming Status", "mdi:ray-start-end"
                    ),
                ]
                async_add_entities(channel_entities, config_subentry_id=subentry_id)


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


class CPPlusChannelStatusSensor(CoordinatorEntity[CPPlusDataUpdateCoordinator], SensorEntity):
    """Read-only status sensor for camera channel configuration parameters."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        """Initialize channel status sensor."""
        super().__init__(coordinator)
        self._channel_idx = channel_idx
        self._channel_num = channel_num
        self._channel_name = channel_name
        self._feature_key = feature_key
        serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{serial}_ch{channel_num}_{feature_key}_status"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = self.coordinator.get_channel_device_info(channel_num, channel_name)

    @property
    def available(self) -> bool:
        """Return true if entity is available."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

    @property
    def icon(self) -> str | None:
        """Return dynamic icon based on state."""
        if not self.coordinator.channels:
            return self._attr_icon
        ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
        if not ch:
            return self._attr_icon
        if self._feature_key == "audio_enable":
            return "mdi:microphone" if ch.get("audio_enable", False) else "mdi:microphone-off"
        if self._feature_key == "smd_human":
            return "mdi:account-search" if ch.get("smd_human", False) else "mdi:account-off"
        if self._feature_key == "smd_vehicle":
            return "mdi:car-search" if ch.get("smd_vehicle", False) else "mdi:car-off"
        if self._feature_key == "tripwire":
            return "mdi:ray-start-end" if ch.get("tripwire", False) else "mdi:ray-end"
        return self._attr_icon

    @property
    def native_value(self) -> str | None:
        """Return the status value."""
        if not self.coordinator.channels:
            return None
        ch = next((c for c in self.coordinator.channels if c.get("index") == self._channel_idx), None)
        if not ch:
            return None
        if self._feature_key == "audio_enable":
            return "Enabled" if ch.get("audio_enable", False) else "Disabled"
        if self._feature_key == "video_in_mode":
            mode = ch.get("video_in_mode", 0)
            return {0: "Color", 1: "Auto", 2: "BlackWhite"}.get(mode, "Unknown")
        if self._feature_key == "lighting_mode":
            return str(ch.get("lighting_mode", "Unknown"))
        if self._feature_key == "smd_human":
            return "Armed" if ch.get("smd_human", False) else "Disarmed"
        if self._feature_key == "smd_vehicle":
            return "Armed" if ch.get("smd_vehicle", False) else "Disarmed"
        if self._feature_key == "tripwire":
            return "Armed" if ch.get("tripwire", False) else "Disarmed"
        return None
