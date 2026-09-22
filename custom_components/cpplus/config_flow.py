"""Config flow for CP PLUS integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult

from homeassistant.helpers import selector

from .client import CPPlusAuthError, CPPlusClient, CPPlusConnectionError, CPPlusError
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_RTSP_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_NAME,
    CONF_DEVICE_TYPE,
    CONF_STREAM_PROFILE,
    CONF_RTSP_OVER_TLS,
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    STREAM_PROFILE_VIDEO_LIVE,
    STREAM_PROFILE_DAHUA_CH0,
    STREAM_PROFILE_DAHUA_CH1,
    STREAM_PROFILE_LIVE,
    STREAM_PROFILE_ONVIF,
    SUBENTRY_TYPE_CHANNEL,
    SUBENTRY_TYPE_HUB,
    TYPE_NVR,
    TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)


def build_device_schema() -> vol.Schema:
    """Build configuration schema for device connections."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME, default="admin"): str,
            vol.Optional(CONF_PASSWORD, default=""): str,
            vol.Optional(CONF_NAME, default=""): str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT_HTTPS): int,
            vol.Optional(CONF_RTSP_PORT, default=DEFAULT_PORT_RTSP): int,
        }
    )


class NVRHubSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for NVR hub settings and renaming."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle user step for NVR hub (hub is a singleton created during initial setup)."""
        return self.async_abort(reason="already_configured")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure the NVR hub subentry title."""
        config_entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            new_name = user_input["name"].strip()
            return self.async_update_and_abort(
                config_entry,
                subentry,
                title=new_name,
                data_updates={"name": new_name},
            )

        schema = vol.Schema(
            {
                vol.Required("name", default=subentry.data.get("name", subentry.title)): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )


class CameraChannelSubentryFlowHandler(ConfigSubentryFlow):
    """Handle subentry flow for adding and modifying camera channels under an NVR."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new camera channel subentry."""
        config_entry = self._get_entry()
        errors: dict[str, str] = {}

        existing_channels = {
            s.data.get("channel")
            for s in config_entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            if s.data.get("channel") is not None
        }

        # Dynamically determine available channels (default up to 32 or max existing)
        max_ch = max(32, max(existing_channels, default=32))
        available_channels = [ch for ch in range(1, max_ch + 1) if ch not in existing_channels]

        if not available_channels:
            return self.async_abort(reason="no_channels_available")

        if user_input is not None:
            ch_num = int(user_input["channel"])
            raw_name = user_input.get("name", "")
            ch_name = raw_name.strip() if raw_name else f"Channel {ch_num}"

            if ch_num in existing_channels:
                errors["channel"] = "channel_exists"
            else:
                return self.async_create_entry(
                    title=ch_name,
                    data={
                        "channel": ch_num,
                        "channel_index": ch_num - 1,
                        "name": ch_name,
                        "is_native_cpplus": True,
                        "has_smd": True,
                        "has_tripwire": False,
                    },
                )

        options = [
            selector.SelectOptionDict(value=str(ch), label=f"Channel {ch}")
            for ch in available_channels
        ]

        schema = vol.Schema(
            {
                vol.Required("channel", default=str(available_channels[0])): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional("name", default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    def __init__(self) -> None:
        """Initialize channel subentry flow."""
        self._reconf_name: str = ""

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing camera channel subentry."""
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            self._reconf_name = user_input["name"].strip() or subentry.title
            if user_input.get("direct_connection", False):
                return await self.async_step_direct_camera()

            # If direct connection is False, save name and clear direct connection settings
            config_entry = self._get_entry()
            return self.async_update_and_abort(
                config_entry,
                subentry,
                title=self._reconf_name,
                data_updates={
                    "name": self._reconf_name,
                    "direct_connection": False,
                    "direct_host": None,
                    "direct_rtsp_port": None,
                    "direct_http_port": None,
                    "use_nvr_credentials": True,
                    "direct_username": None,
                    "direct_password": None,
                    "stream_profile": None,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(
                    "name",
                    default=subentry.data.get("name", subentry.title)
                ): str,
                vol.Required(
                    "direct_connection",
                    default=subentry.data.get("direct_connection", False)
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )

    async def async_step_direct_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Configure direct camera connection options."""
        config_entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}

        # Pre-fill direct_host with discovered address if available
        default_host = subentry.data.get("direct_host") or subentry.data.get("address") or ""
        if not default_host:
            coordinator = self.hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
            if coordinator and getattr(coordinator, "channels", None):
                ch = next((c for c in coordinator.channels if c.get("channel") == subentry.data.get("channel")), None)
                if ch and ch.get("address"):
                    default_host = ch.get("address")

        if user_input is not None:
            direct_host = user_input.get("direct_host", "").strip()
            if not direct_host:
                errors["direct_host"] = "invalid_host"
            else:
                use_nvr_creds = user_input.get("use_nvr_credentials", True)
                direct_user = None if use_nvr_creds else (user_input.get("direct_username", "").strip() or None)
                direct_pass = None if use_nvr_creds else (user_input.get("direct_password", "").strip() or None)
                profile = user_input.get("stream_profile", STREAM_PROFILE_DAHUA_CH1)

                return self.async_update_and_abort(
                    config_entry,
                    subentry,
                    title=self._reconf_name or subentry.title,
                    data_updates={
                        "name": self._reconf_name or subentry.title,
                        "direct_connection": True,
                        "direct_host": direct_host,
                        "direct_rtsp_port": user_input.get("direct_rtsp_port", DEFAULT_PORT_RTSP),
                        "direct_http_port": user_input.get("direct_http_port", 80),
                        "use_nvr_credentials": use_nvr_creds,
                        "direct_username": direct_user,
                        "direct_password": direct_pass,
                        "stream_profile": profile,
                    },
                )

        stream_profiles = [
            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH1, label="Dahua / CP PLUS Realmonitor (/cam/realmonitor?channel=1) [Default]"),
            selector.SelectOptionDict(value=STREAM_PROFILE_VIDEO_LIVE, label="CP PLUS STQC Native (/video/live?channel=1)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH0, label="Dahua Realmonitor (Channel 0)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_ONVIF, label="ONVIF Profile S (/onvif1 Main, /onvif2 Sub)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_LIVE, label="Live Stream (/live)"),
        ]

        schema = vol.Schema(
            {
                vol.Required(
                    "direct_host",
                    default=default_host
                ): str,
                vol.Optional(
                    "stream_profile",
                    default=subentry.data.get("stream_profile", STREAM_PROFILE_DAHUA_CH1)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=stream_profiles,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    "direct_rtsp_port",
                    default=subentry.data.get("direct_rtsp_port") or DEFAULT_PORT_RTSP
                ): int,
                vol.Optional(
                    "direct_http_port",
                    default=subentry.data.get("direct_http_port") or 80
                ): int,
                vol.Required(
                    "use_nvr_credentials",
                    default=subentry.data.get("use_nvr_credentials", True)
                ): bool,
                vol.Optional(
                    "direct_username",
                    default=subentry.data.get("direct_username") or ""
                ): str,
                vol.Optional(
                    "direct_password",
                    default=subentry.data.get("direct_password") or ""
                ): str,
            }
        )
        return self.async_show_form(
            step_id="direct_camera",
            data_schema=schema,
            errors=errors,
        )


class CPPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CP PLUS cameras and NVRs."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: config_entries.ConfigEntry
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Return subentry flows supported by this integration."""
        if config_entry.data.get(CONF_DEVICE_TYPE) == TYPE_NVR:
            return {SUBENTRY_TYPE_CHANNEL: CameraChannelSubentryFlowHandler}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return CPPlusOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Choose connection mode (NVR vs Standalone Camera)."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["nvr", "standalone"],
        )

    async def async_step_nvr(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle NVR setup."""
        return await self._async_handle_device_step(step_id="nvr", expected_type=TYPE_NVR, user_input=user_input)

    async def async_step_standalone(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle standalone IP camera setup."""
        return await self._async_handle_device_step(step_id="standalone", expected_type=TYPE_CAMERA, user_input=user_input)

    async def _async_handle_device_step(
        self, step_id: str, expected_type: str, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Shared logic to authenticate, query hardware metadata, and create entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT_HTTPS)
            rtsp_port = user_input.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP)
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD, "")
            name = user_input.get(CONF_NAME, "").strip() or host

            client = CPPlusClient(
                hass=self.hass,
                host=host,
                port=port,
                rtsp_port=rtsp_port,
                username=username,
                password=password,
                device_type=expected_type,
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial")
                if not serial:
                    raise CPPlusError(f"No serial number returned from device at {host}")
                model = device_info.get("hardware", "STQC")
                detected_type = device_info.get("device_type", expected_type)

                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                await client.async_close()

                if detected_type == TYPE_NVR:
                    if name and name != host:
                        title = f"CP PLUS NVR {name}"
                    else:
                        title = f"CP PLUS NVR {model}" if model else "CP PLUS NVR"
                else:
                    if name and name != host:
                        title = f"CP PLUS Camera {name}"
                    else:
                        title = f"CP PLUS Camera {model}" if model else "CP PLUS Camera"

                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_RTSP_PORT: rtsp_port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_NAME: name,
                        CONF_DEVICE_TYPE: detected_type,
                    },
                )
            except CPPlusAuthError as err:
                _LOGGER.warning("Authentication failed configuring CP PLUS %s: %s", host, err)
                errors["base"] = "invalid_auth"
            except (CPPlusConnectionError, ConnectionError) as err:
                _LOGGER.warning("Connection error configuring CP PLUS %s: %s", host, err)
                errors["base"] = "invalid_auth" if "Authentication failed" in str(err) else "cannot_connect"
            except AbortFlow:
                raise
            except Exception as err:
                _LOGGER.exception("Unexpected exception in CP PLUS config flow: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

        return self.async_show_form(
            step_id=step_id,
            data_schema=build_device_schema(),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle reauthentication upon expired or invalid credentials."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauthentication with updated password."""
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            client = CPPlusClient(
                hass=self.hass,
                host=self._reauth_entry.data[CONF_HOST],
                port=self._reauth_entry.data.get(CONF_PORT, DEFAULT_PORT_HTTPS),
                rtsp_port=self._reauth_entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP),
                username=self._reauth_entry.data[CONF_USERNAME],
                password=password,
                device_type=self._reauth_entry.data.get(CONF_DEVICE_TYPE, TYPE_CAMERA),
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial")
                if serial != self._reauth_entry.unique_id:
                    _LOGGER.error(
                        "Reauth serial mismatch: expected %s, got %s",
                        self._reauth_entry.unique_id,
                        serial,
                    )
                    return self.async_abort(reason="wrong_device")

                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: password,
                    },
                )
            except CPPlusAuthError as err:
                _LOGGER.warning(
                    "Reauth failed for %s on %s: %s",
                    self._reauth_entry.data[CONF_USERNAME],
                    self._reauth_entry.data[CONF_HOST],
                    err,
                )
                errors["base"] = "invalid_auth"
            except (CPPlusConnectionError, ConnectionError) as err:
                _LOGGER.warning("Connection error during reauth: %s", err)
                errors["base"] = "cannot_connect"
            except AbortFlow:
                raise
            except Exception as err:
                _LOGGER.exception("Unexpected exception in reauth: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): str,
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "username": self._reauth_entry.data[CONF_USERNAME],
                "host": self._reauth_entry.data[CONF_HOST],
            },
        )


class CPPlusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for CP PLUS camera and NVR configurations."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage CP PLUS connection and stream options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        device_type = self.config_entry.data.get(CONF_DEVICE_TYPE, TYPE_CAMERA)
        current_profile = self.config_entry.options.get(
            CONF_STREAM_PROFILE,
            self.config_entry.data.get(
                CONF_STREAM_PROFILE,
                STREAM_PROFILE_VIDEO_LIVE if device_type == TYPE_CAMERA else STREAM_PROFILE_DAHUA_CH1,
            ),
        )
        current_tls = self.config_entry.options.get(
            CONF_RTSP_OVER_TLS,
            self.config_entry.data.get(
                CONF_RTSP_OVER_TLS,
                True if device_type == TYPE_CAMERA else False,
            ),
        )
        current_rtsp_port = self.config_entry.options.get(
            CONF_RTSP_PORT,
            self.config_entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP),
        )
        current_port = self.config_entry.options.get(
            CONF_PORT,
            self.config_entry.data.get(CONF_PORT, DEFAULT_PORT_HTTPS),
        )

        stream_profiles = [
            selector.SelectOptionDict(value=STREAM_PROFILE_VIDEO_LIVE, label="CP PLUS STQC Native (/video/live?channel=1) [Default]"),
            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH0, label="Dahua Realmonitor (Channel 0)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH1, label="Dahua Realmonitor (Channel 1 - NVR Default)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_ONVIF, label="ONVIF Profile S (/onvif1 Main, /onvif2 Sub)"),
            selector.SelectOptionDict(value=STREAM_PROFILE_LIVE, label="Live Stream (/live)"),
        ]

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_STREAM_PROFILE,
                    default=current_profile,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=stream_profiles,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_RTSP_OVER_TLS,
                    default=current_tls,
                ): bool,
                vol.Required(
                    CONF_RTSP_PORT,
                    default=current_rtsp_port,
                ): int,
                vol.Required(
                    CONF_PORT,
                    default=current_port,
                ): int,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
