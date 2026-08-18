"""Power switch for the Kirri Beacon.

There is no power command in this protocol. Both directions are the same schedule record -
record 1, every day, all day - differing only in the dispense time: "on" carries the currently
selected work/pause, "off" carries ``work = 0``. Both go through the coordinator, which owns
the intensity that "on" needs - see IntensityMemory.

Off deliberately *holds* record 1 rather than clearing it, because a cleared record hands the
device to whatever the phone app left in records 2-4 instead of stopping it (#27,
protocol.power).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .coordinator import KirriBeaconCoordinator, KirriConfigEntry

if TYPE_CHECKING:
    # Type-only import: this name arrived in HA 2025.2, and there is no reason to make the
    # module fail to load on an older core over an annotation.
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .device import KirriError
from .entity import KirriBeaconEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KirriConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KirriBeaconPowerSwitch(entry.runtime_data)])


class KirriBeaconPowerSwitch(KirriBeaconEntity, SwitchEntity):
    """On/off for the diffuser."""

    # The device's primary control, so it takes the device's own name rather than a suffix.
    _attr_name = None
    # mdi:scent is the diffuser/aroma glyph. The integration's own brand images live in
    # brand/ and cover the integration and device pages; this is the entity-level glyph,
    # which is a separate thing and is not served from there.
    _attr_icon = "mdi:scent"

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool | None:
        """Whether the diffuser is dispensing.

        None until the first successful read - "unknown" is the truth before we have heard
        from the device, and is preferable to guessing "off".

        This is the *device's* state, resolved across every record that had to be read, not
        record 1's day mask. See protocol.DeviceState and #27.
        """
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.is_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        try:
            await self.coordinator.async_set_power(on)
        except KirriError as err:
            # Surfaced to the user rather than swallowed: on this device an accepted write
            # proves nothing, so a missing echo means the command genuinely may not have
            # landed. Reporting success here would be a lie the UI then displays.
            raise HomeAssistantError(
                f"Failed to turn the Kirri Beacon {'on' if on else 'off'}: {err}"
            ) from err
