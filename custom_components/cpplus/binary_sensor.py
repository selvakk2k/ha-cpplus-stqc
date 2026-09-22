"""Binary sensor platform for CP PLUS STQC integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DOMAIN,
    LEGACY_SUBENTRY_TYPE_CHANNEL,
    SUBENTRY_TYPE_CHANNEL,
    TYPE_NVR,
    EVENT_HUMAN,
    EVENT_VEHICLE,
    EVENT_TRIPWIRE,
    EVENT_MOTION,
)
from .coordinator import CPPlusDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CP PLUS binary sensors."""
    coordinator: CPPlusDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Root NVR connectivity sensor (hub level)
    async_add_entities([CPPlusConnectivitySensor(coordinator)])

    if coordinator.client.device_type == TYPE_NVR and coordinator.channels:
        subentries = {
            int(s.data["channel"]): s
            for s in entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            if "channel" in s.data
        }
        for s in entry.get_subentries_of_type(LEGACY_SUBENTRY_TYPE_CHANNEL):
            if "channel" in s.data and int(s.data["channel"]) not in subentries:
                subentries[int(s.data["channel"])] = s
        all_channels = sorted(set(subentries.keys()) | {c["channel"] for c in coordinator.channels})
        for ch_num in all_channels:
            ch = next((c for c in coordinator.channels if c.get("channel") == ch_num), {"channel": ch_num, "index": ch_num - 1, "name": f"Channel {ch_num}", "is_native_cpplus": True, "has_smd": True, "has_tripwire": False})
            ch_idx = ch.get("index", ch_num - 1)
            subentry = subentries.get(ch_num)
            ch_name = (subentry.title if subentry and subentry.title else None) or ch.get("name") or f"Channel {ch_num}"
            subentry_id = subentry.subentry_id if subentry else None

            channel_entities: list[BinarySensorEntity] = [
                CPPlusChannelEventSensor(
                    coordinator=coordinator,
                    channel_idx=ch_idx,
                    channel_num=ch_num,
                    channel_name=ch_name,
                    event_key=EVENT_MOTION,
                    name="Motion",
                    device_class=BinarySensorDeviceClass.MOTION,
                    icon="mdi:motion-sensor",
                )
            ]

            # AI Human Detection sensor
            if ch.get("is_native_cpplus", False) and ch.get("has_smd", False):
                channel_entities.append(
                    CPPlusChannelEventSensor(
                        coordinator=coordinator,
                        channel_idx=ch_idx,
                        channel_num=ch_num,
                        channel_name=ch_name,
                        event_key=EVENT_HUMAN,
                        name="Human Detection",
                        device_class=BinarySensorDeviceClass.MOTION,
                        icon="mdi:account-alert",
                    )
                )

            # AI Vehicle Detection sensor
            if ch.get("is_native_cpplus", False) and ch.get("has_smd", False):
                channel_entities.append(
                    CPPlusChannelEventSensor(
                        coordinator=coordinator,
                        channel_idx=ch_idx,
                        channel_num=ch_num,
                        channel_name=ch_name,
                        event_key=EVENT_VEHICLE,
                        name="Vehicle Detection",
                        device_class=BinarySensorDeviceClass.MOTION,
                        icon="mdi:car",
                    )
                )

            # Perimeter Tripwire sensor
            if ch.get("is_native_cpplus", False) and ch.get("has_tripwire", False):
                channel_entities.append(
                    CPPlusChannelEventSensor(
                        coordinator=coordinator,
                        channel_idx=ch_idx,
                        channel_num=ch_num,
                        channel_name=ch_name,
                        event_key=EVENT_TRIPWIRE,
                        name="Tripwire Breach",
                        device_class=BinarySensorDeviceClass.SAFETY,
                        icon="mdi:ray-start-end",
                    )
                )

            async_add_entities(channel_entities, config_subentry_id=subentry_id)


class CPPlusConnectivitySensor(CoordinatorEntity[CPPlusDataUpdateCoordinator], BinarySensorEntity):
    """Reports camera or NVR network connectivity status."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CPPlusDataUpdateCoordinator) -> None:
        """Initialize connectivity sensor."""
        super().__init__(coordinator)
        serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{serial}_connectivity"
        self._attr_name = "Connection"
        self._attr_device_info = self.coordinator.device_info

    @property
    def is_on(self) -> bool:
        """Return true if the camera or NVR is online."""
        return bool(self.coordinator.data and self.coordinator.data.get("online", False))


class CPPlusChannelEventSensor(CoordinatorEntity[CPPlusDataUpdateCoordinator], BinarySensorEntity):
    """Reports real-time AI and motion events for an NVR camera channel."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CPPlusDataUpdateCoordinator,
        channel_idx: int,
        channel_num: int,
        channel_name: str,
        event_key: str,
        name: str,
        device_class: BinarySensorDeviceClass | None = None,
        icon: str | None = None,
    ) -> None:
        """Initialize event binary sensor."""
        super().__init__(coordinator)
        self._channel_idx = channel_idx
        self._channel_num = channel_num
        self._channel_name = channel_name
        self._event_key = event_key

        nvr_serial = (
            self.coordinator.data.get("serial", self.coordinator.client.host)
            if self.coordinator.data
            else self.coordinator.client.host
        )
        self._attr_unique_id = f"{nvr_serial}_ch{channel_num}_{event_key}"
        self._attr_name = name
        self._attr_device_class = device_class
        if icon:
            self._attr_icon = icon
        self._attr_device_info = self.coordinator.get_channel_device_info(channel_num, channel_name)

    @property
    def is_on(self) -> bool:
        """Return true if the event is active."""
        if not self.coordinator.data:
            return False
        events = self.coordinator.data.get("channel_events", {}).get(self._channel_idx, {})
        return bool(events.get(self._event_key, False))

    @property
    def available(self) -> bool:
        """Return true if NVR is online."""
        return super().available and bool(self.coordinator.data and self.coordinator.data.get("online", False))

