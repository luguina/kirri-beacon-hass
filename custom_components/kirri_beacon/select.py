"""Intensity select for the Kirri Beacon.

Intensity on this model is not a register - it *is* the work/pause ratio (docs/protocol.md,
and Kirri's own documentation, which describes strength as "setting the run and pause times").
So this entity is a shortcut over the two number entities, not a separate control: the four
options are Kirri's preset pairs, and anything else the numbers can produce reads back as
"Custom".

"Custom" is a **readout, not a command**. It only appears in the option list while the values
actually are custom, and selecting it does nothing - there is no such thing as "make it
custom", only "set these seconds", which is what the numbers are for.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import protocol
from .coordinator import KirriBeaconCoordinator, KirriConfigEntry

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .device import KirriError
from .entity import KirriBeaconEntity

_LOGGER = logging.getLogger(__name__)

#: Shown only when the current work/pause pair matches no preset.
CUSTOM_OPTION = "Custom"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KirriConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KirriBeaconIntensitySelect(entry.runtime_data)])


class KirriBeaconIntensitySelect(KirriBeaconEntity, SelectEntity):
    """Delicate / Subtle / Radiant / Intense, plus a read-only "Custom"."""

    _attr_name = "Intensity"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "intensity")

    @property
    def options(self) -> list[str]:
        """The presets, plus "Custom" only while the values really are custom.

        Dynamic rather than fixed so the dropdown never offers an option that cannot do
        anything. Home Assistant validates a selection against this list, so a "Custom" that
        is absent is a "Custom" the user cannot pick by mistake.
        """
        presets = list(protocol.INTENSITY_PRESETS)
        if self.coordinator.intensity_name is None:
            presets.append(CUSTOM_OPTION)
        return presets

    @property
    def current_option(self) -> str:
        return self.coordinator.intensity_name or CUSTOM_OPTION

    async def async_select_option(self, option: str) -> None:
        if option == CUSTOM_OPTION:
            # Reachable only when it is already the current option, so there is nothing to
            # do. Use the Dispense/Pause numbers to change custom values.
            return
        try:
            work, pause = protocol.INTENSITY_PRESETS[option]
        except KeyError as err:  # pragma: no cover - HA validates against options() first
            raise HomeAssistantError(f"Unknown intensity {option!r}") from err

        try:
            # Returns None when the diffuser is off: an intensity frame is also a power-on
            # frame, so the choice is held in Home Assistant until it is switched on.
            await self.coordinator.async_set_intensity(work, pause)
        except KirriError as err:
            raise HomeAssistantError(
                f"Failed to set the Kirri Beacon intensity to {option}: {err}"
            ) from err
