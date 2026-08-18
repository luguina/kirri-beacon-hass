"""Tests for intensity bookkeeping and the entities built on it.

Two rules are worth more than the rest of this file put together, and both are invisible
until somebody presses a button on the wrong day:

* **a zeroed echo must never become the remembered intensity.** Off *is* ``work = 0`` on this
  device (protocol.power), so a diffuser that is off honestly echoes ``work=0``. Absorb that
  and the following power-on dispenses for zero seconds - the diffuser appears to turn on and
  do nothing at all.
* **a restored value must never override the device.** Home Assistant refreshes the
  coordinator before it forwards the platforms, so a diffuser that was already on has told us
  the truth by the time an entity restores its months-old value.

The coordinator itself needs a real hass to construct, so the rules live in IntensityMemory -
plain Python - and are tested here directly. The entities are exercised against a fake
coordinator, which is enough because they hold no state of their own.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

pytest.importorskip("homeassistant", reason="entity tests need Home Assistant installed")

import protocol
from custom_components.kirri_beacon.button import KirriBeaconSyncClockButton
from custom_components.kirri_beacon import coordinator as coordinator_mod
from custom_components.kirri_beacon.coordinator import (
    IntensityMemory,
    PollHealth,
)
from custom_components.kirri_beacon.device import (
    CommandHealth,
    KirriCommandFailed,
)
from custom_components.kirri_beacon.number import (
    KirriBeaconDispenseNumber,
    KirriBeaconPauseNumber,
)
from custom_components.kirri_beacon.select import (
    CUSTOM_OPTION,
    KirriBeaconIntensitySelect,
)
from custom_components.kirri_beacon.sensor import (
    KirriBeaconEchoTimeoutsSensor,
    KirriBeaconFailedPollsSensor,
)
from custom_components.kirri_beacon.switch import KirriBeaconPowerSwitch

ADDRESS = "AA:BB:CC:DD:EE:FF"  # deliberately not the real one


def run(coro):
    return asyncio.run(coro)


def record(*, on: bool = True, work: int = 6, pause: int = 120) -> protocol.ScheduleRecord:
    """Decode a real frame rather than hand-building a dataclass.

    Going through the codec means these tests break if the wire format and the decoder ever
    disagree, instead of quietly testing a fiction.
    """
    return protocol.decode_echo(protocol.echo_for(protocol.power(on, work=work, pause=pause)))


def state(rec: protocol.ScheduleRecord) -> protocol.DeviceState:
    """The device state Home Assistant would hold after writing ``rec`` to record 1.

    Only record 1, because that is genuinely all the coordinator reads once it owns record 1 -
    an all-day record matches, wins arbitration, and makes records 2-4 unreachable.
    """
    return protocol.DeviceState(records={1: rec}, at=dt.datetime(2026, 8, 10, 13, 0))


# ---------------------------------------------------------------------------
# IntensityMemory - the precedence rules
# ---------------------------------------------------------------------------


def test_it_starts_at_the_protocol_default():
    mem = IntensityMemory()
    assert (mem.work, mem.pause) == protocol.INTENSITY_PRESETS[protocol.DEFAULT_INTENSITY]
    assert mem.preset_name == protocol.DEFAULT_INTENSITY
    assert not mem.from_device


def test_an_echo_with_real_values_is_absorbed():
    mem = IntensityMemory()
    assert mem.absorb(record(work=24, pause=120)) is True
    assert (mem.work, mem.pause) == (24, 120)
    assert mem.preset_name == "Intense"
    assert mem.from_device


def test_an_off_echo_does_not_erase_the_intensity():
    """The whole reason this class exists."""
    mem = IntensityMemory()
    mem.absorb(record(work=18, pause=120))
    assert mem.absorb(record(on=False)) is False
    assert (mem.work, mem.pause) == (18, 120)
    assert mem.preset_name == "Radiant"


def test_custom_values_have_no_preset_name():
    mem = IntensityMemory()
    mem.absorb(record(work=7, pause=90))
    assert mem.preset_name is None


def test_a_restore_seeds_values_the_device_has_not_supplied():
    mem = IntensityMemory()
    assert mem.restore(work=24, pause=90) is True
    assert (mem.work, mem.pause) == (24, 90)
    # Restoring is not the same as hearing it from the device, so a later echo still wins.
    assert not mem.from_device


def test_a_restore_never_overrides_the_device():
    mem = IntensityMemory()
    mem.absorb(record(work=12, pause=120))
    assert mem.restore(work=48, pause=600) is False
    assert (mem.work, mem.pause) == (12, 120)


def test_a_restore_can_set_one_field_without_touching_the_other():
    # The two number entities restore independently, so a partial restore has to be safe.
    mem = IntensityMemory()
    mem.restore(work=30)
    assert (mem.work, mem.pause) == (30, protocol.DEFAULT_PAUSE_S)
    mem.restore(pause=45)
    assert (mem.work, mem.pause) == (30, 45)


def test_a_selection_is_held_but_not_treated_as_confirmed():
    mem = IntensityMemory()
    mem.select(33, 200)
    assert (mem.work, mem.pause) == (33, 200)
    assert not mem.from_device


# ---------------------------------------------------------------------------
# PollHealth - how many misses are worth going unavailable over (#38)
# ---------------------------------------------------------------------------


def test_a_fresh_coordinator_has_nothing_to_report():
    health = PollHealth()
    assert (health.consecutive, health.total) == (0, 0)
    assert health.last_error is None and health.last_failure is None
    assert health.may_hold_reading


def test_one_failure_is_tolerated_and_two_are_not():
    """The entire behaviour change, in the object that decides it."""
    health = PollHealth()
    health.failed("connected, but no echo")
    assert health.may_hold_reading
    health.failed("connected, but no echo")
    assert not health.may_hold_reading


def test_a_success_clears_the_streak_but_not_the_total():
    """The total is the measurement channel - a recovery must not erase the evidence."""
    health = PollHealth()
    health.failed("first")
    health.failed("second")
    health.succeeded()
    assert health.consecutive == 0
    assert health.total == 2
    assert health.may_hold_reading
    # And the last failure is still readable afterwards, which is what the sensor exposes.
    assert health.last_error == "second"


def test_the_total_counts_every_failure_not_just_the_visible_ones():
    """The point of counting at all: tolerated misses leave no other trace anywhere."""
    health = PollHealth()
    for _ in range(5):
        health.failed("no echo")
        health.succeeded()  # each one recovered by the next poll, so none was ever visible
    assert health.total == 5
    assert health.consecutive == 0


def test_a_failure_records_when_and_why():
    when = dt.datetime(2026, 8, 13, 20, 0)
    health = PollHealth()
    health.failed("connected, but no echo within 5.0s", at=when)
    assert health.last_error == "connected, but no echo within 5.0s"
    assert health.last_failure == when


def test_one_poll_that_never_ran_voids_tolerance_immediately():
    """An absence is not a miss, so it does not merely cost one of the tolerance's lives.

    Spending a life would leave a short outage - under two poll intervals - inside the budget,
    and the entity would flick back to its pre-outage reading the moment advertisements
    returned, before any poll could confirm it.
    """
    health = PollHealth()
    health.not_attempted()
    assert not health.may_hold_reading, "one skipped poll is enough"


def test_a_poll_that_never_ran_is_not_counted_as_a_fault():
    """Or the measured fault rate would absorb every mains cut."""
    health = PollHealth()
    health.not_attempted()
    health.not_attempted()
    assert health.total == 0
    assert health.last_error is None


def test_only_a_fresh_reading_lifts_an_outage_not_the_device_reappearing():
    """The device coming back says it is there, not what it did while it was gone."""
    health = PollHealth()
    health.not_attempted()
    health.failed("no echo")  # it is advertising again, but the first poll back failed
    assert not health.may_hold_reading
    health.succeeded()
    assert health.may_hold_reading


def test_the_tolerance_is_configurable_by_construction():
    """Not exposed as an option, but the rule must not be welded to the number 1."""
    health = PollHealth(tolerated=0)
    health.failed("no echo")
    assert not health.may_hold_reading


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class FakeDevice:
    """The only part of the transport an entity ever reaches: its counters.

    A real CommandHealth, for the same reason the coordinator below holds a real PollHealth -
    the sensor's job is to report those rules, and a second agreeable implementation of them
    would test nothing.
    """

    def __init__(self) -> None:
        self.health = CommandHealth()

    def echo_lost_then_resent(self, *, write: bool = True) -> None:
        """A command that timed out once and succeeded on the retry - #35's rescued case."""
        self.health.echo_timed_out(write=write)
        self.health.retrying("KirriEchoTimeout: no matching A5FB echo within 5s", echo=True)
        self.health.rescued(echo=True)

    def echo_lost_twice(self, *, write: bool = True) -> None:
        """A command the retry could not save, so the caller got an error."""
        self.health.echo_timed_out(write=write)
        self.health.retrying("KirriEchoTimeout: no matching A5FB echo within 5s", echo=True)
        self.health.echo_timed_out(write=write)
        self.health.exhausted(echo=True)

    def stale_link_then_reconnect(self) -> None:
        """The *other* retry cause, which must not land in the echo columns."""
        self.health.retrying("BleakError: not connected", echo=False)
        self.health.rescued(echo=False)


class FakeCoordinator:
    """Stands in for the coordinator, which cannot be built without a real hass.

    Holds a genuine IntensityMemory so the entities are tested against the real rules rather
    than against a second, agreeable implementation of them.
    """

    def __init__(self, *, on: bool = True, fail: bool = False) -> None:
        self.address = ADDRESS
        self.intensity = IntensityMemory()
        self.data = state(record(on=on))
        self.last_update_success = True
        self.device_present = True
        self.fail = fail
        self.applied: list[tuple[int, int]] = []
        self.clock_syncs = 0
        # A real one, for the same reason IntensityMemory is real: availability is decided by
        # its rules, and a second agreeable implementation of them would test nothing.
        self.health = PollHealth()
        # The transport keeps its own counters one layer down, because they cover reads and
        # writes alike where PollHealth only sees polls. See CommandHealth.
        self.device = FakeDevice()

    def poll_failed(self, error: str = "connected, but no echo") -> None:
        """What the coordinator does to itself when a poll raises. Data is left standing."""
        self.health.failed(error)
        self.last_update_success = False

    def poll_skipped(self) -> None:
        """What the coordinator does when the device is not advertising - nothing is tried."""
        self.health.not_attempted()
        self.last_update_success = False

    def poll_succeeded(self) -> None:
        self.health.succeeded()
        self.last_update_success = True

    # -- the surface the entities use --------------------------------------------------
    @property
    def work_s(self) -> int:
        return self.intensity.work

    @property
    def pause_s(self) -> int:
        return self.intensity.pause

    @property
    def intensity_name(self):
        return self.intensity.preset_name

    def async_restore_intensity(self, *, work=None, pause=None) -> None:
        self.intensity.restore(work=work, pause=pause)

    async def async_set_intensity(self, work: int, pause: int):
        if self.fail:
            raise KirriCommandFailed("no echo")
        self.applied.append((work, pause))
        if self.data is None or not self.data.is_on:
            self.intensity.select(work, pause)
            return None
        # A real write comes back as an echo, and that echo is what updates the memory.
        echoed = record(work=work, pause=pause)
        self.intensity.absorb(echoed)
        self.data = state(echoed)
        return self.data

    async def async_sync_clock(self) -> None:
        if self.fail:
            raise KirriCommandFailed("not reachable")
        self.clock_syncs += 1

    # -- CoordinatorEntity plumbing ----------------------------------------------------
    def async_add_listener(self, _update_callback, _context=None):
        return lambda: None


def test_the_select_offers_only_the_presets_while_a_preset_is_active():
    entity = KirriBeaconIntensitySelect(FakeCoordinator())
    assert entity.options == list(protocol.INTENSITY_PRESETS)
    assert CUSTOM_OPTION not in entity.options
    assert entity.current_option == "Delicate"


def test_custom_appears_only_once_the_values_are_custom():
    coordinator = FakeCoordinator()
    entity = KirriBeaconIntensitySelect(coordinator)
    coordinator.intensity.select(7, 90)
    assert entity.current_option == CUSTOM_OPTION
    assert entity.options[-1] == CUSTOM_OPTION


def test_selecting_a_preset_applies_its_work_and_pause():
    coordinator = FakeCoordinator()
    entity = KirriBeaconIntensitySelect(coordinator)
    run(entity.async_select_option("Intense"))
    assert coordinator.applied == [(24, 120)]
    assert entity.current_option == "Intense"


def test_selecting_custom_writes_nothing():
    """It is a readout of the current values, not an instruction."""
    coordinator = FakeCoordinator()
    coordinator.intensity.select(7, 90)
    entity = KirriBeaconIntensitySelect(coordinator)
    run(entity.async_select_option(CUSTOM_OPTION))
    assert coordinator.applied == []
    assert (coordinator.work_s, coordinator.pause_s) == (7, 90)


def test_a_failed_intensity_write_raises_rather_than_reporting_success():
    from homeassistant.exceptions import HomeAssistantError

    entity = KirriBeaconIntensitySelect(FakeCoordinator(fail=True))
    with pytest.raises(HomeAssistantError, match="Intense"):
        run(entity.async_select_option("Intense"))


def test_the_numbers_read_the_remembered_values():
    coordinator = FakeCoordinator()
    coordinator.intensity.absorb(record(work=18, pause=120))
    assert KirriBeaconDispenseNumber(coordinator).native_value == 18
    assert KirriBeaconPauseNumber(coordinator).native_value == 120


def test_setting_dispense_keeps_the_current_pause():
    coordinator = FakeCoordinator()
    coordinator.intensity.absorb(record(work=6, pause=200))
    run(KirriBeaconDispenseNumber(coordinator).async_set_native_value(30))
    assert coordinator.applied == [(30, 200)]


def test_setting_pause_keeps_the_current_dispense():
    coordinator = FakeCoordinator()
    coordinator.intensity.absorb(record(work=18, pause=120))
    run(KirriBeaconPauseNumber(coordinator).async_set_native_value(45))
    assert coordinator.applied == [(18, 45)]


def test_the_number_bounds_cover_every_preset():
    """A preset the slider cannot express would be a select the numbers contradict."""
    coordinator = FakeCoordinator()
    dispense = KirriBeaconDispenseNumber(coordinator)
    pause = KirriBeaconPauseNumber(coordinator)
    for work, pause_s in protocol.INTENSITY_PRESETS.values():
        assert dispense.native_min_value <= work <= dispense.native_max_value
        assert pause.native_min_value <= pause_s <= pause.native_max_value


def test_an_intensity_change_while_off_is_held_and_not_written_to_the_device():
    """Every intensity frame is also a power-on frame, so writing one would switch it on."""
    coordinator = FakeCoordinator(on=False)
    entity = KirriBeaconIntensitySelect(coordinator)
    run(entity.async_select_option("Radiant"))
    assert coordinator.work_s == 18
    assert not coordinator.data.is_on  # still off


# ---------------------------------------------------------------------------
# Availability - what a failed poll costs (#38)
# ---------------------------------------------------------------------------


def test_a_single_failed_poll_leaves_the_entity_available_on_its_last_reading():
    """#38: this used to be 900 s of `unavailable` for a miss the next poll recovered."""
    coordinator = FakeCoordinator(on=True)
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.poll_failed()
    assert entity.available
    assert entity.is_on, "the held reading is the whole point of staying available"


def test_two_consecutive_failed_polls_take_it_unavailable():
    coordinator = FakeCoordinator()
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.poll_failed()
    coordinator.poll_failed()
    assert not entity.available


def test_a_device_that_stopped_advertising_is_unavailable_however_healthy_the_polls():
    """The hard gate. Tolerating a miss is only honest while something else says it is there."""
    coordinator = FakeCoordinator()
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.device_present = False
    assert not entity.available
    coordinator.poll_failed()  # and one tolerated miss must not rescue it either
    assert not entity.available


def test_a_device_back_from_an_outage_waits_for_a_reading_rather_than_showing_the_old_one():
    """Advertising again says it is there, not what it did while it was gone.

    A single skipped poll would otherwise sit inside the tolerance budget, so a short outage
    would end with the entity flicking back to its pre-outage state seconds before the
    refresh that could confirm it.
    """
    coordinator = FakeCoordinator(on=True)
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.device_present = False
    coordinator.poll_skipped()
    assert not entity.available

    coordinator.device_present = True
    assert not entity.available, "still nothing has confirmed the state"
    coordinator.poll_succeeded()
    assert entity.available


def test_the_next_successful_poll_restores_availability():
    coordinator = FakeCoordinator()
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.poll_failed()
    coordinator.poll_failed()
    assert not entity.available
    coordinator.poll_succeeded()
    assert entity.available
    assert coordinator.health.consecutive == 0


def test_a_failure_before_the_first_reading_is_not_tolerated():
    """Tolerating a miss means holding the previous state. There isn't one yet.

    This is the setup path: __init__ refreshes rather than raising ConfigEntryNotReady, so a
    diffuser that is silent at startup must give entities that exist and read `unavailable`.
    """
    coordinator = FakeCoordinator()
    coordinator.data = None
    entity = KirriBeaconPowerSwitch(coordinator)
    coordinator.poll_failed()
    assert not entity.available


def test_the_failed_poll_sensor_stays_available_when_everything_else_does_not():
    """A counter that hides whenever the device does would hide its most useful readings."""
    coordinator = FakeCoordinator()
    sensor = KirriBeaconFailedPollsSensor(coordinator)
    switch = KirriBeaconPowerSwitch(coordinator)
    coordinator.device_present = False
    coordinator.poll_failed()
    coordinator.poll_failed()
    assert not switch.available
    assert sensor.available


def test_the_failed_poll_sensor_counts_misses_the_entity_never_showed():
    """Without this the fix erases its own evidence - see sensor.py."""
    coordinator = FakeCoordinator()
    sensor = KirriBeaconFailedPollsSensor(coordinator)
    switch = KirriBeaconPowerSwitch(coordinator)
    for _ in range(3):
        coordinator.poll_failed()
        assert switch.available, "each miss is tolerated, so it never reaches the recorder"
        coordinator.poll_succeeded()
    assert sensor.native_value == 3
    assert sensor.extra_state_attributes["consecutive"] == 0


def test_the_failed_poll_sensor_reports_the_streak_and_the_reason():
    coordinator = FakeCoordinator()
    sensor = KirriBeaconFailedPollsSensor(coordinator)
    coordinator.poll_failed("connected, but no echo within 5.0s")
    attributes = sensor.extra_state_attributes
    assert attributes["consecutive"] == 1
    assert attributes["tolerated"] == coordinator.health.tolerated
    assert attributes["last_error"] == "connected, but no echo within 5.0s"
    assert attributes["last_failure"] is not None


def test_the_failed_poll_sensor_starts_at_zero_rather_than_unknown():
    """A diagnostic that reads `unknown` until the first fault is one you cannot trust."""
    sensor = KirriBeaconFailedPollsSensor(FakeCoordinator())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["last_failure"] is None


# ---------------------------------------------------------------------------
# CommandHealth and its sensor - #35's meter (ADR-013)
# ---------------------------------------------------------------------------


def test_a_fresh_transport_has_no_faults_to_report():
    health = CommandHealth()
    assert (health.echo_timeouts, health.echo_retries, health.echo_recovered,
            health.echo_failures, health.other_retries) == (0, 0, 0, 0, 0)
    assert (health.write_echo_timeouts, health.read_echo_timeouts) == (0, 0)
    assert health.last_retry_error is None and health.last_retry is None


def test_a_stale_link_retry_never_lands_in_the_echo_columns():
    """Otherwise the meter tells a story about a fault that did not happen.

    `_async_run` retries a stale link as well as a missing echo. Counted into one pair, a
    window with no missing echoes at all could report "0 timeouts, 3 rescued by the retry" —
    which is precisely the kind of confident nonsense this counter exists to prevent.
    """
    health = CommandHealth()
    health.retrying("BleakError: not connected", echo=False)
    health.rescued(echo=False)

    assert health.other_retries == 1
    assert (health.echo_timeouts, health.echo_retries, health.echo_recovered) == (0, 0, 0)
    # Still visible as the most recent cause, so one sample can explain itself.
    assert "BleakError" in health.last_retry_error


def test_an_echo_retry_that_then_fails_on_the_link_is_still_an_echo_failure():
    """The outcome is filed against what *sent us round again*, not what attempt 2 hit.

    A missing echo followed by a dropped link on the resend is an echo-driven retry that did
    not work; scoring it as a link fault would quietly shrink the number #35 is judged on.
    """
    health = CommandHealth()
    health.echo_timed_out(write=True)
    health.retrying("KirriEchoTimeout: no matching A5FB echo within 5s", echo=True)
    health.exhausted(echo=True)  # attempt 2 died on a BleakError, but the cause was the echo

    assert (health.echo_retries, health.echo_failures) == (1, 1)
    assert health.other_retries == 0


def test_the_read_and_write_halves_always_sum_to_the_total():
    """Derived rather than stored, so the split cannot drift away from the number it splits.

    This is the measurement docs/lab/soak-2026-08.md could not make: it argued reads and writes are
    one defect from two overlapping confidence intervals over a *derived* poll denominator.
    """
    health = CommandHealth()
    for _ in range(4):
        health.echo_timed_out(write=True)
    for _ in range(7):
        health.echo_timed_out(write=False)
    assert (health.write_echo_timeouts, health.read_echo_timeouts) == (4, 7)
    assert health.write_echo_timeouts + health.read_echo_timeouts == health.echo_timeouts == 11


def test_every_retry_ends_in_exactly_one_of_recovered_or_failed():
    """`retries == recovered + failures` is what makes a single sample readable.

    Without it, a reader seeing 9 retries and 4 recoveries cannot tell whether 5 failed or
    whether 5 are still in flight.
    """
    health = CommandHealth()
    for _ in range(3):
        health.retrying("KirriEchoTimeout: silence", echo=True)
        health.rescued(echo=True)
    for _ in range(2):
        health.retrying("KirriEchoTimeout: silence", echo=True)
        health.exhausted(echo=True)
    assert health.echo_retries == health.echo_recovered + health.echo_failures == 5


def test_a_retry_records_when_and_why():
    when = dt.datetime(2026, 8, 13, 20, 0)
    health = CommandHealth()
    health.retrying("KirriEchoTimeout: no matching A5FB echo within 5s", echo=True, at=when)
    assert health.last_retry == when
    assert "KirriEchoTimeout" in health.last_retry_error


def test_the_echo_timeout_sensor_reports_the_fault_not_the_policy():
    """The state is the count that keeps its meaning if the retry policy ever changes.

    `retries`, `recovered` and `failures` all describe what we chose to do about the fault;
    only `echo_timeouts` describes the device and the link, which is why it is the series
    exported to long-term statistics.
    """
    coordinator = FakeCoordinator()
    sensor = KirriBeaconEchoTimeoutsSensor(coordinator)
    coordinator.device.echo_lost_then_resent()
    coordinator.device.echo_lost_twice()

    assert sensor.native_value == 3, "one rescued fault plus two from the command that failed"
    attributes = sensor.extra_state_attributes
    assert (attributes["retries"], attributes["recovered"], attributes["failures"]) == (2, 1, 1)
    assert "KirriEchoTimeout" in attributes["last_retry_error"]
    assert attributes["last_retry"] is not None


def test_the_sensor_keeps_other_retry_causes_out_of_the_echo_attributes():
    """A stale-link retry must not read as a rescued echo in a soak report."""
    coordinator = FakeCoordinator()
    sensor = KirriBeaconEchoTimeoutsSensor(coordinator)
    coordinator.device.stale_link_then_reconnect()

    attributes = sensor.extra_state_attributes
    assert sensor.native_value == 0, "no echo went missing"
    assert (attributes["retries"], attributes["recovered"]) == (0, 0)
    assert attributes["other_retries"] == 1, "but the retry path was used, and says so"


def test_the_echo_timeout_sensor_splits_reads_from_writes():
    """The comparison the #14 soak had to reach indirectly, counted directly instead."""
    coordinator = FakeCoordinator()
    sensor = KirriBeaconEchoTimeoutsSensor(coordinator)
    coordinator.device.echo_lost_then_resent(write=False)  # a poll
    coordinator.device.echo_lost_then_resent(write=True)  # somebody pressing the switch

    attributes = sensor.extra_state_attributes
    assert (attributes["on_reads"], attributes["on_writes"]) == (1, 1)
    assert attributes["on_reads"] + attributes["on_writes"] == sensor.native_value


def test_the_echo_timeout_sensor_records_a_fault_nothing_else_shows():
    """The reason the fix ships with a meter at all (ADR-013).

    A rescued command is a success to every other entity in the integration and to the user
    who issued it. Before #35 that same fault was a visible failure every time, so leaving it
    uncounted would make a working retry and a quiet week look identical.
    """
    coordinator = FakeCoordinator(on=True)
    sensor = KirriBeaconEchoTimeoutsSensor(coordinator)
    switch = KirriBeaconPowerSwitch(coordinator)

    coordinator.device.echo_lost_then_resent()
    coordinator.poll_succeeded()  # the retry worked, so the poll it belonged to succeeded

    assert switch.available, "nothing the user can see went wrong"
    assert coordinator.health.total == 0, "and the poll counter has nothing to report either"
    assert sensor.native_value == 1, "but the fault happened, and here it is"


def test_the_echo_timeout_sensor_stays_available_when_everything_else_does_not():
    coordinator = FakeCoordinator()
    sensor = KirriBeaconEchoTimeoutsSensor(coordinator)
    switch = KirriBeaconPowerSwitch(coordinator)
    coordinator.device_present = False
    coordinator.poll_failed()
    coordinator.poll_failed()
    assert not switch.available
    assert sensor.available


def test_the_echo_timeout_sensor_starts_at_zero_rather_than_unknown():
    sensor = KirriBeaconEchoTimeoutsSensor(FakeCoordinator())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["last_retry"] is None


def test_the_two_diagnostics_do_not_collide_in_the_entity_registry():
    """Same device, same platform - a shared unique_id would silently drop one of them."""
    coordinator = FakeCoordinator()
    assert (
        KirriBeaconFailedPollsSensor(coordinator).unique_id
        != KirriBeaconEchoTimeoutsSensor(coordinator).unique_id
    )


# ---------------------------------------------------------------------------
# resolve_state - what the device is doing, and what it costs to find out
# ---------------------------------------------------------------------------


class StubDevice:
    """A diffuser holding a fixed table of records, counting what gets asked of it."""

    def __init__(self, table: dict[int, protocol.ScheduleRecord]) -> None:
        self.table = table
        self.batches: list[tuple[int, ...]] = []

    async def async_query_records(self, records):
        self.batches.append(tuple(records))
        return {rc: self.table[rc] for rc in records}


def _app_records() -> dict[int, protocol.ScheduleRecord]:
    """The phone app's three default schedules, as read off the device on 2026-08-09."""
    def rec(record, days, start, end, work, pause):
        return protocol.decode_echo(protocol.echo_for(protocol.schedule(
            enabled=True, days=days, record=record,
            start=start, end=end, work=work, pause=pause,
        )))

    return {
        1: rec(1, protocol.ALL_DAYS, (10, 0), (12, 0), 6, 120),
        2: rec(2, protocol.ALL_DAYS, (12, 0), (18, 0), 6, 120),
        3: rec(3, protocol.ALL_DAYS, (18, 0), (22, 0), 6, 120),
        4: rec(4, protocol.NO_DAYS, (0, 0), (0, 0), 0, 0),
    }


AFTERNOON = dt.datetime(2026, 8, 10, 13, 0)  # a Monday, and the hour #27 was reported at


@pytest.mark.parametrize("on", [True, False])
def test_an_owned_record_one_costs_no_extra_radio(on):
    """The poll budget is unchanged by this fix, in both switch positions.

    Every frame power() builds matches at every instant, so nothing behind record 1 is
    reachable and there is nothing worth connecting for.
    """
    device = StubDevice(_app_records())
    resolved = run(coordinator_mod.resolve_state(device, record(on=on), AFTERNOON))
    assert device.batches == []
    assert resolved.is_on is on
    assert resolved.is_owned


def test_a_record_one_that_does_not_match_makes_us_read_the_rest():
    """And this is #27: the state is unknowable from record 1 alone, so ask."""
    table = _app_records()
    device = StubDevice(table)
    resolved = run(coordinator_mod.resolve_state(device, table[1], AFTERNOON))
    assert device.batches == [(2, 3, 4)]
    # Record 1's own window closed at 12:00, so the device fell through to Afternoons.
    assert resolved.is_on
    assert resolved.active.record == 2
    assert not resolved.is_owned


def test_a_cleared_record_one_still_finds_the_app_schedule_running():
    """The literal failure: HA zeroed record 1, read back zeros, and reported off."""
    table = _app_records()
    cleared = protocol.decode_echo(
        bytes.fromhex(protocol.RETIRED_OFF["echo"])
    )
    device = StubDevice(table)
    resolved = run(coordinator_mod.resolve_state(device, cleared, AFTERNOON))
    assert device.batches == [(2, 3, 4)]
    assert resolved.is_on, "a cleared record 1 falls through - it does not switch off"


def test_the_off_frame_stops_the_device_even_with_app_schedules_present():
    """The fix, at the layer Home Assistant actually reads state from."""
    device = StubDevice(_app_records())
    resolved = run(coordinator_mod.resolve_state(device, record(on=False), AFTERNOON))
    assert device.batches == [], "record 1 holds the line; 2-4 are unreachable"
    assert not resolved.is_on
    assert resolved.active.record == 1


def test_the_button_sends_a_clock_sync():
    coordinator = FakeCoordinator()
    run(KirriBeaconSyncClockButton(coordinator).async_press())
    assert coordinator.clock_syncs == 1


def test_a_failed_clock_sync_surfaces_as_an_error():
    from homeassistant.exceptions import HomeAssistantError

    entity = KirriBeaconSyncClockButton(FakeCoordinator(fail=True))
    with pytest.raises(HomeAssistantError, match="clock"):
        run(entity.async_press())
