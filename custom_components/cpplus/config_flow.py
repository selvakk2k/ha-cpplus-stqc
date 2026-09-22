"""Config flow for CP PLUS integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries, core
from homeassistant.data_entry_flow import FlowResult

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
    STREAM_PROFILE_DAHUA_CH0,
    STREAM_PROFILE_DAHUA_CH1,
    STREAM_PROFILE_LIVE,
    STREAM_PROFILE_ONVIF,
    STREAM_PROFILE_VIDEO_LIVE,
    SUBENTRY_TYPE_CHANNEL,
    TYPE_NVR,
    TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)


class CameraChannelSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Handle camera channel subentry creation and modifications."""

    def __init__(self) -> None:
        """Initialize channel subentry flow."""
        self._channel: int = 1
        self._channel_name: str = ""
        self._is_reconfigure: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle adding a new camera channel subentry."""
        config_entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            ch_num = user_input["channel"]
            ch_name = user_input.get("channel_name", "").strip() or f"Channel {ch_num}"
            existing_channels = {
                s.data.get("channel")
                for s in config_entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            }
            if ch_num in existing_channels:
                errors["channel"] = "channel_exists"
            else:
                self._channel = ch_num
                self._channel_name = ch_name
                self._is_reconfigure = False

                if user_input.get("direct_connection", False):
                    return await self.async_step_direct_camera()

                return self.async_create_entry(
                    title=ch_name,
                    data={
                        "channel": ch_num,
                        "channel_index": ch_num - 1,
                        "name": ch_name,
                        "is_native_cpplus": True,
                        "has_smd": True,
                        "has_tripwire": False,
                        "direct_connection": False,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required("channel", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
                vol.Optional("channel_name", default=""): str,
                vol.Optional("direct_connection", default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguring a channel name and direct connection."""
        subentry = self._get_reconfigure_subentry()
        current_name = subentry.title or subentry.data.get("name", "")
        current_direct = subentry.data.get("direct_connection", False)

        if user_input is not None:
            new_name = user_input.get("channel_name", "").strip() or current_name
            self._channel = subentry.data.get("channel", 1)
            self._channel_name = new_name
            self._is_reconfigure = True

            if user_input.get("direct_connection", False):
                return await self.async_step_direct_camera()

            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=new_name,
                data={
                    **subentry.data,
                    "name": new_name,
                    "direct_connection": False,
                },
            )

        schema = vol.Schema(
            {
                vol.Required("channel_name", default=current_name): str,
                vol.Optional("direct_connection", default=current_direct): bool,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"channel": str(subentry.data.get("channel", ""))},
        )

    async def async_step_direct_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure direct camera IP address and stream parameters."""
        config_entry = self._get_entry()
        errors: dict[str, str] = {}

        existing_subentry = self._get_reconfigure_subentry() if self._is_reconfigure else None
        current_data = dict(existing_subentry.data) if existing_subentry else {}

        default_host = current_data.get("direct_host") or current_data.get("address") or ""
        default_rtsp = current_data.get("direct_rtsp_port", DEFAULT_PORT_RTSP)
        default_use_nvr = current_data.get("use_nvr_credentials", True)
        default_user = current_data.get("direct_username", "")
        default_pass = current_data.get("direct_password", "")

        if user_input is not None:
            direct_host = user_input.get("direct_host", "").strip()
            if not direct_host:
                errors["direct_host"] = "invalid_host"
            else:
                use_nvr = user_input.get("use_nvr_credentials", True)
                direct_user = "" if use_nvr else user_input.get("direct_username", "").strip()
                direct_pass = "" if use_nvr else user_input.get("direct_password", "").strip()
                direct_rtsp_port = user_input.get("direct_rtsp_port", DEFAULT_PORT_RTSP)

                if self._is_reconfigure and existing_subentry:
                    return self.async_update_and_abort(
                        config_entry,
                        existing_subentry,
                        title=self._channel_name or existing_subentry.title,
                        data={
                            **existing_subentry.data,
                            "name": self._channel_name or existing_subentry.title,
                            "direct_connection": True,
                            "direct_host": direct_host,
                            "direct_rtsp_port": direct_rtsp_port,
                            "use_nvr_credentials": use_nvr,
                            "direct_username": direct_user,
                            "direct_password": direct_pass,
                        },
                    )

                return self.async_create_entry(
                    title=self._channel_name,
                    data={
                        "channel": self._channel,
                        "channel_index": self._channel - 1,
                        "name": self._channel_name,
                        "is_native_cpplus": True,
                        "has_smd": True,
                        "has_tripwire": False,
                        "direct_connection": True,
                        "direct_host": direct_host,
                        "direct_rtsp_port": direct_rtsp_port,
                        "use_nvr_credentials": use_nvr,
                        "direct_username": direct_user,
                        "direct_password": direct_pass,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required("direct_host", default=default_host): str,
                vol.Optional("direct_rtsp_port", default=default_rtsp): int,
                vol.Required("use_nvr_credentials", default=default_use_nvr): bool,
                vol.Optional("direct_username", default=default_user): str,
                vol.Optional("direct_password", default=default_pass): str,
            }
        )

        return self.async_show_form(
            step_id="direct_camera",
            data_schema=schema,
            errors=errors,
        )


class CPPlusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle CP PLUS integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the CP PLUS options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        device_type = self.config_entry.data.get(CONF_DEVICE_TYPE, TYPE_CAMERA)
        current_rtsp = self.config_entry.options.get(
            CONF_RTSP_PORT,
            self.config_entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP),
        )
        current_profile = self.config_entry.options.get(
            CONF_STREAM_PROFILE,
            STREAM_PROFILE_DAHUA_CH1 if device_type == TYPE_NVR else STREAM_PROFILE_VIDEO_LIVE,
        )
        current_tls = self.config_entry.options.get(
            CONF_RTSP_OVER_TLS,
            self.config_entry.data.get(CONF_RTSP_OVER_TLS, False),
        )

        schema = vol.Schema(
            {
                vol.Optional(CONF_RTSP_PORT, default=current_rtsp): int,
                vol.Optional(
                    CONF_STREAM_PROFILE,
                    default=current_profile,
                ): vol.In(
                    {
                        STREAM_PROFILE_DAHUA_CH1: "Dahua Channel 1-32 (/cam/realmonitor?channel=X)",
                        STREAM_PROFILE_DAHUA_CH0: "Dahua Channel 0 (/cam/realmonitor?channel=0)",
                        STREAM_PROFILE_VIDEO_LIVE: "CP PLUS Native (/video/live)",
                        STREAM_PROFILE_ONVIF: "ONVIF Native (/onvif1, /onvif2)",
                        STREAM_PROFILE_LIVE: "Live Direct (/live)",
                    }
                ),
                vol.Optional(CONF_RTSP_OVER_TLS, default=current_tls): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


class CPPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CP PLUS cameras and NVRs."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._reconfigure_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @core.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return CPPlusOptionsFlowHandler()

    @staticmethod
    @core.callback
    def async_get_supported_subentry_types(
        config_entry: config_entries.ConfigEntry,
    ) -> dict[str, type[config_entries.SubentryFlowHandler]]:
        """Return supported subentry types."""
        if config_entry.data.get(CONF_DEVICE_TYPE) == TYPE_CAMERA:
            return {}
        return {
            SUBENTRY_TYPE_CHANNEL: CameraChannelSubentryFlowHandler,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT_HTTPS)
            rtsp_port = user_input.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP)
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD, "")
            name = user_input.get(CONF_NAME, "").strip() or host
            selected_device_type = user_input.get(CONF_DEVICE_TYPE, TYPE_NVR)

            client = CPPlusClient(
                hass=self.hass,
                host=host,
                port=port,
                rtsp_port=rtsp_port,
                username=username,
                password=password,
                device_type=selected_device_type,
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial")
                if not serial:
                    raise CPPlusError(f"No serial number returned from device at {host}")
                model = device_info.get("hardware", "STQC")
                device_type = selected_device_type

                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()

                await client.async_close()

                if device_type == TYPE_NVR:
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
                        CONF_DEVICE_TYPE: device_type,
                    },
                )
            except CPPlusAuthError as err:
                _LOGGER.warning("Authentication failed configuring CP PLUS %s: %s", host, err)
                errors["base"] = "invalid_auth"
            except (CPPlusConnectionError, ConnectionError) as err:
                _LOGGER.warning("Connection error configuring CP PLUS %s: %s", host, err)
                errors["base"] = "invalid_auth" if "Authentication failed" in str(err) else "cannot_connect"
            except config_entries.AbortFlow:
                raise
            except Exception as err:
                _LOGGER.exception("Unexpected exception in CP PLUS config flow: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME, default="admin"): str,
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Optional(CONF_NAME, default=""): str,
                vol.Required(CONF_DEVICE_TYPE, default=TYPE_NVR): vol.In(
                    {
                        TYPE_NVR: "NVR / DVR Hub (Multi-channel)",
                        TYPE_CAMERA: "Standalone IP Camera (Single stream)",
                    }
                ),
                vol.Optional(CONF_PORT, default=DEFAULT_PORT_HTTPS): int,
                vol.Optional(CONF_RTSP_PORT, default=DEFAULT_PORT_RTSP): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguring the main device credentials and host."""
        self._reconfigure_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reconfigure_confirm()

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reconfigure."""
        errors: dict[str, str] = {}
        assert self._reconfigure_entry is not None

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT_HTTPS)
            rtsp_port = user_input.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP)
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD, "")

            client = CPPlusClient(
                hass=self.hass,
                host=host,
                port=port,
                rtsp_port=rtsp_port,
                username=username,
                password=password,
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial")
                if serial != self._reconfigure_entry.unique_id:
                    return self.async_abort(reason="wrong_device")

                return self.async_update_reload_and_abort(
                    self._reconfigure_entry,
                    data={
                        **self._reconfigure_entry.data,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_RTSP_PORT: rtsp_port,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )
            except CPPlusAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=self._reconfigure_entry.data.get(CONF_HOST, ""),
                ): str,
                vol.Required(
                    CONF_USERNAME,
                    default=self._reconfigure_entry.data.get(CONF_USERNAME, "admin"),
                ): str,
                vol.Optional(
                    CONF_PASSWORD,
                    default=self._reconfigure_entry.data.get(CONF_PASSWORD, ""),
                ): str,
                vol.Optional(
                    CONF_PORT,
                    default=self._reconfigure_entry.data.get(CONF_PORT, DEFAULT_PORT_HTTPS),
                ): int,
                vol.Optional(
                    CONF_RTSP_PORT,
                    default=self._reconfigure_entry.data.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP),
                ): int,
            }
        )

        return self.async_show_form(
            step_id="reconfigure_confirm",
            data_schema=schema,
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
            except config_entries.AbortFlow:
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
