"""Dispense and pause timings for the Kirri Beacon.

These two numbers *are* the intensity - the device has no intensity register, only a burst
length and a gap (docs/protocol.md). The select is a shortcut over the presets; this is the
full surface, and it is what makes a non-preset setting reachable at all.

**These entities are also where intensity survives a Home Assistant restart.** They restore
their own last value and hand it to the coordinator, which is the correct place for it: they
carry the actual seconds, whereas the select only carries a preset name and could not express
a custom pair. The coordinator refuses the restore if the diffuser has already told us
something better - see IntensityMemory.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberDeviceClass, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    MAX_DISPENSE_S,
    MAX_PAUSE_S,
    MIN_DISPENSE_S,
    MIN_PAUSE_S,
)
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
    coordinator = entry.runtime_data
    async_add_entities(
        [
            KirriBeaconDispenseNumber(coordinator),
            KirriBeaconPauseNumber(coordinator),
        ]
    )


class KirriBeaconTimingNumber(KirriBeaconEntity, RestoreNumber):
    """Shared behaviour for the two timing numbers."""

    # Secondary to the switch and the select, which are the controls you reach for daily.
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_step = 1

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_number_data()
        if last is None or last.native_value is None:
            return
        seconds = int(last.native_value)
        if not self.native_min_value <= seconds <= self.native_max_value:
            # Only reachable if the bounds in const.py were tightened since the value was
            # stored. Dropping it is safer than clamping: a clamped value is a number the
            # user never chose, presented as though they did.
            _LOGGER.debug(
                "%s: ignoring restored %s of %ss, outside %s-%ss",
                self.coordinator.address, self.name, seconds,
                self.native_min_value, self.native_max_value,
            )
            return
        self._restore(seconds)

    def _restore(self, seconds: int) -> None:
        raise NotImplementedError

    async def _async_apply(self, work: int, pause: int, label: str) -> None:
        try:
            await self.coordinator.async_set_intensity(work, pause)
        except KirriError as err:
            raise HomeAssistantError(
                f"Failed to set the Kirri Beacon {label}: {err}"
            ) from err


class KirriBeaconDispenseNumber(KirriBeaconTimingNumber):
    """How long each burst of mist lasts."""

    _attr_name = "Dispense time"
    _attr_icon = "mdi:spray"
    _attr_native_min_value = MIN_DISPENSE_S
    _attr_native_max_value = MAX_DISPENSE_S
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "dispense_seconds")

    @property
    def native_value(self) -> int:
        return self.coordinator.work_s

    def _restore(self, seconds: int) -> None:
        self.coordinator.async_restore_intensity(work=seconds)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_apply(int(value), self.coordinator.pause_s, "dispense time")


class KirriBeaconPauseNumber(KirriBeaconTimingNumber):
    """How long the diffuser rests between bursts."""

    _attr_name = "Pause time"
    _attr_icon = "mdi:timer-pause-outline"
    _attr_native_min_value = MIN_PAUSE_S
    _attr_native_max_value = MAX_PAUSE_S
    # A box, not a slider: the useful range spans two orders of magnitude, so a slider would
    # make the common values (around 120 s) the hardest ones to land on.
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "pause_seconds")

    @property
    def native_value(self) -> int:
        return self.coordinator.pause_s

    def _restore(self, seconds: int) -> None:
        self.coordinator.async_restore_intensity(pause=seconds)

    async def async_set_native_value(self, value: float) -> None:
        await self._async_apply(self.coordinator.work_s, int(value), "pause time")
