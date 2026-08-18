"""Manual clock sync for the Kirri Beacon.

The integration already syncs the clock on connect, at most once an hour (const.py). This
button exists for the case that rate limit cannot cover: the diffuser's RTC free-runs, and
after a mains cut it comes back holding whatever it feels like, so being able to say "resync,
now" without waiting out an hour is worth one button.

On-device schedules were dropped in favour of Home Assistant automations (#17, ADR-009), so
nothing here reads that RTC any more - the power path uses an all-day/all-days window where it
is never consulted. The button stays anyway: it costs nothing, and it is the only lever left if
the clock ever does turn out to matter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .coordinator import KirriBeaconCoordinator, KirriConfigEntry

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .device import KirriError
from .entity import KirriBeaconEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KirriConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KirriBeaconSyncClockButton(entry.runtime_data)])


class KirriBeaconSyncClockButton(KirriBeaconEntity, ButtonEntity):
    """Send the current wall-clock time to the diffuser."""

    _attr_name = "Sync clock"
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "sync_clock")

    async def async_press(self) -> None:
        """Send the sync.

        ⚠️ Unlike every other command here, **this one cannot be confirmed.** The device never
        acknowledges the clock frame (verified on hardware 2026-08-09), so a press that raises
        nothing means the write was accepted - not that the clock was set. That is the entire
        readback available; whether the device applied it is now **permanently unknown**. The
        one experiment that could have shown it - put a real time window on a record, watch
        when it fires - went away with on-device schedules (#17, ADR-009).
        """
        try:
            await self.coordinator.async_sync_clock()
        except KirriError as err:
            raise HomeAssistantError(
                f"Failed to sync the Kirri Beacon clock: {err}"
            ) from err
        _LOGGER.debug("%s: clock sync sent by hand", self.coordinator.address)
