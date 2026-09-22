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
    """Handle camera channel subentry modifications."""

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguring a channel name."""
        subentry = self._get_reconfigure_subentry()
        current_name = subentry.title or subentry.data.get("name", "")

        if user_input is not None:
            new_name = user_input.get("channel_name", "").strip() or current_name
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=new_name,
                data={
                    **subentry.data,
                    "name": new_name,
                },
            )

        schema = vol.Schema(
            {
                vol.Required("channel_name", default=current_name): str,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"channel": str(subentry.data.get("channel", ""))},
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
                if not serial:
                    raise CPPlusError(f"No serial number returned from device at {host}")
                model = device_info.get("hardware", "STQC")
                device_type = device_info.get("device_type", TYPE_CAMERA)

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
