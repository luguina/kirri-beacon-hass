"""Diagnostics about the integration, not readings from the diffuser.

This platform exists because of what #38 and #35 cost to fix. Both fixes are the same shape:
they stop a real fault from being visible, and every failure number this project has was
*inferred from what the faults did to the UI* - 13 dropouts in 72 h, 4.5%, the interval that
reconciles reads with writes in docs/lab/soak-2026-08.md - because neither a poll nor a retry leaves
any other trace. The box has no persistent Home Assistant log to fall back on
(`/api/error_log` 404s on this install).

So each fix ships with its own instrument:

* **Failed polls** (#38, ADR-012) - a tolerated miss no longer shows as `unavailable`;
* **Echo timeouts** (#35, ADR-013) - a retried command no longer shows as a failure.

Without them a verification soak would report a clean box because the failures had been
hidden, not because they had stopped - each fix measured against a meter the same commit
broke.

This is *not* the sensor platform #13 asked for. Those need the 21-byte live-status frame,
which has never been observed on air (docs/protocol.md, "Still unknown"), and shipping
permanently-`unknown` entities is worse than shipping none. Nothing here reads the device.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant

from .coordinator import KirriBeaconCoordinator, KirriConfigEntry

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from .entity import KirriBeaconEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KirriConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        [
            KirriBeaconFailedPollsSensor(entry.runtime_data),
            KirriBeaconEchoTimeoutsSensor(entry.runtime_data),
        ]
    )


class KirriBeaconDiagnosticSensor(KirriBeaconEntity, SensorEntity):
    """A counter about the integration itself.

    Always available, and that is the point of the base class. A counter that goes
    `unavailable` whenever the device does is a counter that hides its most interesting
    readings - including the second consecutive failure, which is the exact event that took
    every other entity down.

    It is also why these are entities rather than attributes on the switch: Home Assistant
    strips attributes from an `unavailable` state, so a switch attribute would vanish at
    precisely the moment worth recording.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Gets the value into long-term statistics, which survive the recorder's purge window. A
    # soak measured over weeks can then be read back even after the raw state rows are gone -
    # so whatever a subclass exports as its state should be the number worth keeping that long.
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def available(self) -> bool:
        """Always. This reports on the integration, not on the diffuser."""
        return True


class KirriBeaconFailedPollsSensor(KirriBeaconDiagnosticSensor):
    """How many status polls have failed since the integration loaded."""

    _attr_name = "Failed polls"
    _attr_icon = "mdi:radar"

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "failed_polls")

    @property
    def native_value(self) -> int:
        """Total failures since the config entry loaded.

        Resets to 0 on a reload or a Home Assistant restart, and that is deliberate - a
        visible reset is more honest than a running total whose origin nobody can pin down.
        Readers should sum the positive increments across a window rather than subtracting
        the endpoints; tools/soak/soak.py does.
        """
        return self.coordinator.health.total

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Enough context to read a single sample without going back to the code."""
        health = self.coordinator.health
        return {
            # The streak, which is what actually decides availability.
            "consecutive": health.consecutive,
            "tolerated": health.tolerated,
            "last_error": health.last_error,
            "last_failure": health.last_failure.isoformat() if health.last_failure else None,
        }


class KirriBeaconEchoTimeoutsSensor(KirriBeaconDiagnosticSensor):
    """How many commands the device answered with silence, and what the retry did (#35).

    Sits one layer below the failed-poll counter, in the transport, so it sees **reads and
    writes alike**. That matters: docs/lab/soak-2026-08.md had to argue that one defect explains both
    from a derived poll denominator and two overlapping confidence intervals. This counts them
    directly.
    """

    _attr_name = "Echo timeouts"
    _attr_icon = "mdi:message-alert-outline"

    def __init__(self, coordinator: KirriBeaconCoordinator) -> None:
        super().__init__(coordinator, "echo_timeouts")

    @property
    def native_value(self) -> int:
        """Attempts whose A5FB echo never arrived, rescued by the retry or not.

        This one rather than the retry count, because it is the only number here that does not
        describe our own retry policy. Change the policy - two retries, a backoff, anything -
        and `retries`, `recovered` and `failures` all change meaning; this keeps describing the
        device and the link, so it is the series worth having in long-term statistics.

        Resets to 0 on a reload or a Home Assistant restart, deliberately: a visible reset is
        more honest than a running total whose origin nobody can pin down. Readers should sum
        the positive increments across a window rather than subtracting the endpoints;
        tools/soak/soak.py does.
        """
        return self.coordinator.device.health.echo_timeouts

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the retry made of those timeouts.

        `retries`, `recovered` and `failures` cover **only** commands retried because of a
        missing echo. That scoping is load-bearing: `_async_run` also retries a stale link,
        and folding both into one pair would let a stale-link retry report itself as a rescued
        echo - so a window with no missing echoes could read "0 timeouts, 3 rescued". Retries
        from any other cause are counted separately as `other_retries`, without an outcome
        breakdown, because that fault predates this work and still surfaces on its own terms.

        `recovered` is the number #35 is judged on - commands that failed once and succeeded
        on the resend, which before the fix were user-visible failures every time. `failures`
        is what still got through to the caller, and `retries` is always the sum of the two.

        `on_reads` / `on_writes` split the state above by what the command was doing.
        docs/lab/soak-2026-08.md argues reads and writes are one defect from two overlapping
        confidence intervals and a poll denominator that had to be *derived* from the interval
        rather than measured; these two count the populations directly, so the re-soak can
        settle it. They always sum to the state.
        """
        health = self.coordinator.device.health
        return {
            "retries": health.echo_retries,
            "recovered": health.echo_recovered,
            "failures": health.echo_failures,
            "other_retries": health.other_retries,
            "on_reads": health.read_echo_timeouts,
            "on_writes": health.write_echo_timeouts,
            "last_retry_error": health.last_retry_error,
            "last_retry": health.last_retry.isoformat() if health.last_retry else None,
        }
