"""Config flow for the Kirri Beacon.

Discovery has an awkward constraint worth stating up front: **the diffuser puts almost
nothing on air.** Its name (`AJBLE100`) lives only in the scan response and in GATT 0x2A00,
neither of which reaches a Home Assistant matcher through a proxy - 0 occurrences in 20 raw
advertisement packets (docs/research/dossier.md 6.1). The only on-air discriminator is
service UUID 0xFFF0, which this device advertises, does not implement, and shares with a
large population of unrelated cheap BLE hardware.

So the manifest matcher is necessarily broad, and identity is settled by *connecting*: a
real Beacon answers a status query with an A5FB echo. Nothing else will.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac

from .const import (
    CONF_DRAIN_SECONDS,
    CONF_POLL_INTERVAL,
    DEFAULT_DRAIN_SECONDS,
    DEFAULT_POLL_INTERVAL_S,
    DOMAIN,
    MAX_DRAIN_SECONDS,
    MAX_POLL_INTERVAL_S,
    MIN_POLL_INTERVAL_S,
    VALIDATE_MAX_ATTEMPTS,
)
from .device import KirriBeaconDevice, KirriNotFound, KirriNotSupported

_LOGGER = logging.getLogger(__name__)

DEFAULT_TITLE = "Kirri Beacon Diffuser"
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


async def _async_validate(hass: HomeAssistant, address: str) -> None:
    """Prove there is a Kirri Beacon at this address.

    Runs a real status query rather than only checking that the vendor service exists,
    because that exercises the whole path the integration depends on - proxy routing,
    notification subscription, and the echo - and it is the echo that proves the protocol,
    not the service UUID.
    """
    device = KirriBeaconDevice(
        hass, address, drain_seconds=0, max_attempts=VALIDATE_MAX_ATTEMPTS
    )
    try:
        record = await device.async_query()
        _LOGGER.debug("%s: validated, device holds %s", address, record)
    finally:
        await device.async_shutdown()


class KirriBeaconConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a diffuser, by discovery or by address."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_address: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle an advertisement matching the manifest.

        This will fire for unrelated FFF0 devices too - see the module docstring - so the
        confirmation step is where identity actually gets established.
        """
        # Normalised on the way in, so an entry created by discovery is byte-identical to one
        # created by hand - entity unique_ids and presence lookups both key off this.
        address = discovery_info.address.upper()
        await self.async_set_unique_id(format_mac(address))
        self._abort_if_unique_id_configured()
        self._discovered_address = address
        self.context["title_placeholders"] = {"name": f"{DEFAULT_TITLE} ({discovery_info.address})"}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovered_address is not None
        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(
                step_id="bluetooth_confirm",
                description_placeholders={"address": self._discovered_address},
            )

        try:
            await _async_validate(self.hass, self._discovered_address)
        except KirriNotSupported:
            # Almost certainly some other device that also advertises FFF0. Aborting is the
            # honest outcome - there is nothing the user can do to make it a Beacon.
            return self.async_abort(reason="not_supported")
        except KirriNotFound:
            return self.async_abort(reason="cannot_connect")
        except Exception:
            _LOGGER.exception("Unexpected error validating %s", self._discovered_address)
            return self.async_abort(reason="unknown")

        return self.async_create_entry(
            title=DEFAULT_TITLE, data={CONF_ADDRESS: self._discovered_address}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add by address.

        Kept as a first-class path rather than a fallback: after a power cut the diffuser is
        off and not advertising at all (#8), so there are real situations where discovery
        cannot help and typing the address is the only way in.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if not _MAC_RE.match(address):
                errors["base"] = "invalid_address"
            else:
                await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                try:
                    await _async_validate(self.hass, address)
                except KirriNotFound:
                    errors["base"] = "cannot_connect"
                except KirriNotSupported:
                    errors["base"] = "not_supported"
                except Exception:
                    _LOGGER.exception("Unexpected error validating %s", address)
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(
                        title=DEFAULT_TITLE, data={CONF_ADDRESS: address}
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS, default=self._suggested_address()): str}
            ),
            errors=errors,
        )

    def _suggested_address(self) -> str:
        """Pre-fill with a candidate if exactly one is on air, otherwise leave it blank."""
        configured = {
            str(entry.data.get(CONF_ADDRESS, "")).upper()
            for entry in self._async_current_entries()
        }
        candidates = [
            info.address.upper()
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address.upper() not in configured
        ]
        return candidates[0] if len(candidates) == 1 else ""

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return KirriBeaconOptionsFlow()


class KirriBeaconOptionsFlow(OptionsFlow):
    """Two tunables, both of which trade responsiveness against radio time."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_S),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL_S, max=MAX_POLL_INTERVAL_S),
                    ),
                    vol.Required(
                        CONF_DRAIN_SECONDS,
                        default=options.get(CONF_DRAIN_SECONDS, DEFAULT_DRAIN_SECONDS),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0, max=MAX_DRAIN_SECONDS)),
                }
            ),
        )
