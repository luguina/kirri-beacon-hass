"""Shared entity base for the Kirri Beacon."""

from __future__ import annotations

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import KirriBeaconCoordinator


class KirriBeaconEntity(CoordinatorEntity[KirriBeaconCoordinator]):
    """Base class wiring every entity to one diffuser."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KirriBeaconCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            # format_mac so the registry does not end up with two devices for the same
            # diffuser if the address is ever stored in a different case.
            connections={(CONNECTION_BLUETOOTH, format_mac(coordinator.address))},
            name="Kirri Beacon Diffuser",
            manufacturer=MANUFACTURER,
            model=MODEL,
            # No Device Information service on this device (docs/protocol.md), so there is
            # no firmware or hardware version to report. Leaving them unset is honest;
            # inventing them is not.
        )

    @property
    def available(self) -> bool:
        """On air, and either fresh or stale by no more than one missed poll.

        Two independent questions, deliberately kept apart because they are answered by
        different mechanisms at wildly different cost:

        **Is the diffuser there?** ``device_present``, from Home Assistant's advertisement
        tracking - free, and accurate within seconds. It stays a hard gate: nothing below
        can make an entity available while the device is off air. CoordinatorEntity's own
        check would only notice up to 15 minutes late, and after a power cut that difference
        is the whole point.

        **Is our reading usable?** Normally ``last_update_success``. But a single failed poll
        used to flip that false and leave it false for a full interval - 900 s of
        ``unavailable`` for a miss the very next poll recovered, measured 13 times in the 72-hour
        #14 soak (#38). So one miss is tolerated and the previous reading is held; see ADR-012
        for why that is honest rather than a lie, and PollHealth for the counting.

        ``data is not None`` is the precondition, not a formality: tolerating a failure *means*
        holding the previous reading, so with no previous reading there is nothing to hold and
        the entity is honestly unavailable. That is also the setup path - __init__ deliberately
        refreshes rather than raising ConfigEntryNotReady, so a diffuser that is silent at
        startup gets entities that exist and read ``unavailable`` (#8, #15).
        """
        if not self.coordinator.device_present:
            return False
        # super() rather than last_update_success directly, so "is the reading fresh" stays
        # Home Assistant's definition and this class only owns the two additions.
        if super().available:
            return True
        return self.coordinator.data is not None and self.coordinator.health.may_hold_reading
