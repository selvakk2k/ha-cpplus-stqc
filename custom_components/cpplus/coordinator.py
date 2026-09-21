"""DataUpdateCoordinator for CP PLUS STQC cameras and NVRs."""

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from .client import CPPlusAuthError, CPPlusClient
from .const import (
    DOMAIN,
    MANUFACTURER,
    TYPE_NVR,
    EVENT_HUMAN,
    EVENT_VEHICLE,
    EVENT_TRIPWIRE,
    EVENT_MOTION,
)

_LOGGER = logging.getLogger(__name__)


class CPPlusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage CP PLUS camera and NVR state updates."""

    def __init__(self, hass: HomeAssistant, client: CPPlusClient, name: str) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"CP PLUS STQC {name}",
            update_interval=timedelta(seconds=30),
        )
        self.client = client
        self.device_name = name
        self.device_info_data: dict[str, Any] = {}
        self.channels: list[dict[str, Any]] = []
        self.channel_events: dict[int, dict[str, bool]] = {}
        self.parent_device_id: str | None = None
        self._last_channel_refresh: float = 0.0

    def handle_event(self, channel_idx: int, event_code: str, action: str) -> None:
        """Process push event from NVR event stream."""
        is_active = (action.lower() == "start")
        events = self.channel_events.setdefault(channel_idx, {})
        target_event = None

        if event_code in ["SmartMotionHuman", "HumanDetect"]:
            target_event = EVENT_HUMAN
        elif event_code in ["SmartMotionVehicle", "VehicleDetect"]:
            target_event = EVENT_VEHICLE
        elif event_code in ["CrossLineDetection", "CrossRegionDetection"]:
            target_event = EVENT_TRIPWIRE
        elif event_code in ["VideoMotion"]:
            target_event = EVENT_MOTION

        has_changed = False
        if target_event:
            old_val = events.get(target_event)
            if old_val != is_active:
                events[target_event] = is_active
                has_changed = True
                _LOGGER.debug("Channel %d %s alert: %s", channel_idx, target_event, is_active)

        # Fire native Home Assistant event for automation triggers
        ch = next((c for c in self.channels if c.get("index") == channel_idx or c.get("channel") == channel_idx + 1), {})
        ch_name = ch.get("name") or f"Channel {channel_idx + 1}"
        self.hass.bus.async_fire(
            "cpplus_event",
            {
                "channel": channel_idx + 1,
                "channel_index": channel_idx,
                "channel_name": ch_name,
                "event_type": event_code,
                "action": action,
            },
        )

        # Notify entity listeners immediately on actual state toggle
        if has_changed and self.data:
            new_data = dict(self.data)
            new_data["channel_events"] = dict(self.channel_events)
            self.async_set_updated_data(new_data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest camera or NVR telemetry."""
        try:
            if not self.device_info_data:
                self.device_info_data = await self.client.async_get_device_info()

            if self.client.device_type == TYPE_NVR:
                now = time.monotonic()
                if not self.channels or (now - self._last_channel_refresh >= 600):
                    try:
                        new_channels = await self.client.async_get_channels()
                        if new_channels:
                            self.channels = new_channels
                            self._last_channel_refresh = now
                    except CPPlusAuthError:
                        raise
                    except Exception as err:
                        _LOGGER.debug(
                            "Channel refresh failed on %s (preserving current channels): %s",
                            self.client.host,
                            err,
                        )

                # Lightweight connection ping
                await self.client.async_nvr_request("/cgi-bin/magicBox.cgi?action=getDeviceType")
            else:
                # Periodic ping / keepalive query to test connection on standalone camera
                await self.client.async_call_rpc("magicBox.getDeviceType")

            return {
                "online": True,
                "serial": self.device_info_data.get("serial", self.client.host),
                "hardware": self.device_info_data.get("hardware", "CP PLUS STQC"),
                "firmware": self.device_info_data.get("firmware", "Unknown"),
                "device_type": self.client.device_type,
                "channels": self.channels,
                "channel_events": self.channel_events,
            }
        except CPPlusAuthError as err:
            _LOGGER.warning("Authentication failed on %s: %s", self.client.host, err)
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except Exception as err:
            _LOGGER.warning("Coordinator update error on %s: %s", self.client.host, err)
            raise UpdateFailed(f"Error communicating with CP PLUS device at {self.client.host}: {err}") from err

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant DeviceInfo for the main NVR or standalone camera."""
        serial = self.device_info_data.get("serial") or self.client.host
        model = self.device_info_data.get("hardware") or ("CP-UNR-4K4322-V4" if self.client.device_type == TYPE_NVR else "CP-UNC-TA21L3C-Q")
        firmware = self.device_info_data.get("firmware") or "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"CP PLUS STQC {self.device_name}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=firmware,
            configuration_url=f"https://{self.client.host}:{self.client.port}",
        )

    def get_channel_device_info(self, channel_num: int, channel_name: str) -> DeviceInfo:
        """Return Home Assistant DeviceInfo for a child camera channel connected to the NVR."""
        nvr_serial = self.device_info_data.get("serial") or self.client.host
        channel_unique_id = f"{nvr_serial}_ch{channel_num}"

        ch = next((c for c in self.channels if c.get("channel") == channel_num), {})
        model = ch.get("model") or "Camera"
        manufacturer = ch.get("manufacturer") or ("CP PLUS" if model.startswith("CP-") else ("Dahua" if model.startswith("VTO") else ("Xiongmai" if model.startswith("IPC_GK") else "Generic ONVIF")))
        serial = ch.get("serial")
        firmware = ch.get("firmware")
        addr = ch.get("address")
        https_port = ch.get("https_port")
        http_port = ch.get("http_port")

        if addr:
            if https_port and https_port not in ("0", "443"):
                config_url = f"https://{addr}:{https_port}"
            elif https_port == "443":
                config_url = f"https://{addr}"
            elif http_port and http_port not in ("0", "80"):
                config_url = f"http://{addr}:{http_port}"
            else:
                config_url = f"http://{addr}"
        else:
            config_url = f"https://{self.client.host}:{self.client.port}"

        info = DeviceInfo(
            identifiers={(DOMAIN, channel_unique_id)},
            name=f"{channel_name}",
            manufacturer=manufacturer,
            model=model,
            configuration_url=config_url,
        )
        if firmware:
            info["sw_version"] = firmware
        if serial:
            info["serial_number"] = serial
        if self.parent_device_id:
            info["via_device_id"] = self.parent_device_id
        return info

