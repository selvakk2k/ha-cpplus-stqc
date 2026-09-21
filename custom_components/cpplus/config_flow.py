"""Config flow for CP PLUS integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
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
    DEFAULT_PORT_HTTPS,
    DEFAULT_PORT_RTSP,
    TYPE_NVR,
    TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)


class CPPlusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CP PLUS cameras and NVRs."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

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
