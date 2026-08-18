"""The Kirri Beacon integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DRAIN_SECONDS,
    CONF_POLL_INTERVAL,
    DEFAULT_DRAIN_SECONDS,
    DEFAULT_POLL_INTERVAL_S,
)
from .coordinator import KirriBeaconCoordinator, KirriConfigEntry
from .device import KirriBeaconDevice

_LOGGER = logging.getLogger(__name__)

# The sensor platform carries diagnostics about the integration only - see sensor.py. The two
# "remaining" sensors #13 describes are still not here and still should not be: they need the
# 21-byte live-status frame, which has never been observed on air (docs/protocol.md, "Still
# unknown"), and entities that would be permanently `unknown` are worse than no entities.
PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: KirriConfigEntry) -> bool:
    """Set up a diffuser from a config entry."""
    address: str = entry.data[CONF_ADDRESS]

    device = KirriBeaconDevice(
        hass,
        address,
        drain_seconds=entry.options.get(CONF_DRAIN_SECONDS, DEFAULT_DRAIN_SECONDS),
    )
    coordinator = KirriBeaconCoordinator(
        hass,
        entry,
        device,
        poll_interval=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_S),
    )
    coordinator.async_start_presence_tracking()
    entry.runtime_data = coordinator

    # Deliberately async_refresh(), *not* async_config_entry_first_refresh().
    #
    # first_refresh raises ConfigEntryNotReady when the device does not answer, which puts
    # the whole entry into setup_retry and removes its entities from the dashboard. For this
    # device that is the wrong shape: after a mains cut the diffuser is off and silent until
    # somebody presses its base button (#8), which is a human-scale wait. The useful
    # behaviour is entities that exist and read `unavailable`, so you can see the state and
    # #15 can alert on it - not entities that vanish.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        _LOGGER.info(
            "%s did not answer during setup - entities will be unavailable until it does. "
            "If this follows a power cut, the diffuser needs its base button pressed",
            address,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KirriConfigEntry) -> bool:
    """Tear down, releasing the BLE link and the advertisement subscription."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        coordinator.async_shutdown_tracking()  # advertisement callback + presence timer
        await coordinator.async_shutdown()  # the coordinator's own refresh timer
        await coordinator.device.async_shutdown()  # the BLE link itself
    return unload_ok


async def _async_reload_on_options_change(
    hass: HomeAssistant, entry: KirriConfigEntry
) -> None:
    # Both options are constructor arguments (poll interval, drain window), so a reload is
    # the simplest correct way to apply them.
    await hass.config_entries.async_reload(entry.entry_id)
