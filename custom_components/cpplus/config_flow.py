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


from homeassistant.helpers import selector


def build_channel_subentry_schema(
    default_channel: int = 1,
    default_name: str = "",
    default_direct_host: str = "",
    default_rtsp_port: int = DEFAULT_PORT_RTSP,
    default_username: str = "",
    default_password: str = "",
    is_reconfigure: bool = False,
) -> vol.Schema:
    """Build unified schema for camera channel subentry."""
    fields: dict[Any, Any] = {}
    if not is_reconfigure:
        fields[vol.Required("channel", default=default_channel)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=32, step=1, mode=selector.NumberSelectorMode.BOX)
        )
    fields[vol.Optional("channel_name", default=default_name)] = selector.TextSelector()
    fields[vol.Optional("direct_host", default=default_direct_host)] = selector.TextSelector()
    fields[vol.Optional("direct_rtsp_port", default=default_rtsp_port)] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
    )
    fields[vol.Optional("direct_username", default=default_username)] = selector.TextSelector()
    fields[vol.Optional("direct_password", default=default_password)] = selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )
    return vol.Schema(fields)


def build_device_schema() -> vol.Schema:
    """Build device configuration schema using Home Assistant selectors."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): selector.TextSelector(),
            vol.Required(CONF_USERNAME, default="admin"): selector.TextSelector(),
            vol.Optional(CONF_PASSWORD, default=""): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_NAME, default=""): selector.TextSelector(),
            vol.Optional(CONF_PORT, default=DEFAULT_PORT_HTTPS): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_RTSP_PORT, default=DEFAULT_PORT_RTSP): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


class CameraChannelSubentryFlowHandler(config_entries.ConfigSubentryFlow):
    """Handle camera channel subentry creation and modifications."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle adding a new camera channel subentry."""
        config_entry = self._get_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            ch_num = int(user_input["channel"])
            ch_name = user_input.get("channel_name", "").strip() or f"Channel {ch_num}"
            existing_channels = {
                s.data.get("channel")
                for s in config_entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
            }
            if ch_num in existing_channels:
                errors["channel"] = "channel_exists"
            else:
                direct_host = user_input.get("direct_host", "").strip()
                direct_conn = bool(direct_host)
                direct_rtsp = int(user_input.get("direct_rtsp_port") or DEFAULT_PORT_RTSP)
                direct_user = user_input.get("direct_username", "").strip() or None
                direct_pass = user_input.get("direct_password", "").strip() or None

                return self.async_create_entry(
                    title=ch_name,
                    data={
                        "channel": ch_num,
                        "channel_index": ch_num - 1,
                        "name": ch_name,
                        "is_native_cpplus": True,
                        "has_smd": True,
                        "has_tripwire": False,
                        "direct_connection": direct_conn,
                        "direct_host": direct_host if direct_conn else None,
                        "direct_rtsp_port": direct_rtsp,
                        "direct_username": direct_user,
                        "direct_password": direct_pass,
                    },
                )

        existing_channels = {
            s.data.get("channel")
            for s in config_entry.get_subentries_of_type(SUBENTRY_TYPE_CHANNEL)
        }
        next_ch = next((c for c in range(1, 33) if c not in existing_channels), 1)

        return self.async_show_form(
            step_id="user",
            data_schema=build_channel_subentry_schema(default_channel=next_ch),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguring a channel name and stream options."""
        config_entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        current_name = subentry.title or subentry.data.get("name", "")
        current_data = dict(subentry.data)

        if user_input is not None:
            new_name = user_input.get("channel_name", "").strip() or current_name
            direct_host = user_input.get("direct_host", "").strip()
            direct_conn = bool(direct_host)
            direct_rtsp = int(user_input.get("direct_rtsp_port") or DEFAULT_PORT_RTSP)
            direct_user = user_input.get("direct_username", "").strip() or None
            direct_pass = user_input.get("direct_password", "").strip() or None

            return self.async_update_and_abort(
                config_entry,
                subentry,
                title=new_name,
                data={
                    **subentry.data,
                    "name": new_name,
                    "direct_connection": direct_conn,
                    "direct_host": direct_host if direct_conn else None,
                    "direct_rtsp_port": direct_rtsp,
                    "direct_username": direct_user,
                    "direct_password": direct_pass,
                },
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=build_channel_subentry_schema(
                default_name=current_name,
                default_direct_host=current_data.get("direct_host") or "",
                default_rtsp_port=current_data.get("direct_rtsp_port", DEFAULT_PORT_RTSP),
                default_username=current_data.get("direct_username") or "",
                default_password=current_data.get("direct_password") or "",
                is_reconfigure=True,
            ),
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
                vol.Optional(CONF_RTSP_PORT, default=current_rtsp): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(
                    CONF_STREAM_PROFILE,
                    default=current_profile,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH1, label="Dahua Channel 1-32 (/cam/realmonitor?channel=X)"),
                            selector.SelectOptionDict(value=STREAM_PROFILE_DAHUA_CH0, label="Dahua Channel 0 (/cam/realmonitor?channel=0)"),
                            selector.SelectOptionDict(value=STREAM_PROFILE_VIDEO_LIVE, label="CP PLUS Native (/video/live)"),
                            selector.SelectOptionDict(value=STREAM_PROFILE_ONVIF, label="ONVIF Native (/onvif1, /onvif2)"),
                            selector.SelectOptionDict(value=STREAM_PROFILE_LIVE, label="Live Direct (/live)"),
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_RTSP_OVER_TLS, default=current_tls): selector.BooleanSelector(),
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
        """Step 1: Choose setup mode (NVR vs Standalone Camera)."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["nvr", "camera"],
        )

    async def async_step_nvr(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle NVR setup."""
        return await self._async_handle_device_step("nvr", TYPE_NVR, user_input)

    async def async_step_camera(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle standalone IP camera setup."""
        return await self._async_handle_device_step("camera", TYPE_CAMERA, user_input)

    async def _async_handle_device_step(
        self, step_id: str, device_type: str, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Shared logic to authenticate, query hardware metadata, and create entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT_HTTPS))
            rtsp_port = int(user_input.get(CONF_RTSP_PORT, DEFAULT_PORT_RTSP))
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
                device_type=device_type,
            )

            try:
                device_info = await client.async_get_device_info()
                serial = device_info.get("serial")
                if not serial:
                    raise CPPlusError(f"No serial number returned from device at {host}")
                model = device_info.get("hardware", "STQC")

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
            except CPPlusError as err:
                _LOGGER.warning("Device error configuring CP PLUS %s: %s", host, err)
                errors["base"] = "cannot_connect"
            except config_entries.AbortFlow:
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
