"""State and availability for one Kirri Beacon.

Two signals, deliberately kept separate because they cost very different amounts:

* **availability comes from advertisements**, via Home Assistant's own presence tracking —
  free, no radio, no connection. This matters because "gone silent" is the *expected* state
  after a mains cut (#8), and it is what #15's alert will hang off. Measured on 2026-08-09:
  the entity goes unavailable about **5.5 minutes** after the device stops advertising, and
  recovers within **~8 seconds** of it coming back. Note we ask HA rather than timing
  advertisements ourselves; see device_present for why that distinction cost an afternoon.
* **state comes from the 0xFB echo** — which every write returns anyway, so the slow poll
  below is only a safety net, not the primary mechanism.

⚠️ **State is a property of the device, not of record 1.** The diffuser runs the first record
by id whose day mask and time window match now, and falls through when one does not
(protocol.active_record) — so "what does record 1 say" and "what is the diffuser doing" are
different questions on any unit the phone app has touched. Treating them as the same question
was #27: Home Assistant read a truthful zero from record 1 and reported off while record 2 ran
the atomiser. _async_resolve is where the difference lives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol
from .const import DEFAULT_POLL_INTERVAL_S, DOMAIN, TOLERATED_POLL_FAILURES
from .device import KirriBeaconDevice, KirriError

_LOGGER = logging.getLogger(__name__)

type KirriConfigEntry = ConfigEntry["KirriBeaconCoordinator"]


async def resolve_state(
    device: KirriBeaconDevice, record_1: protocol.ScheduleRecord, now: datetime
) -> protocol.DeviceState:
    """Turn record 1 into a device state, reading records 2-4 only when it does not decide.

    **The common path still costs exactly one query.** Every frame protocol.power() builds
    covers all seven days from 00:00 to 23:59, so while Home Assistant owns record 1 that
    record matches at every instant, wins arbitration, and makes records 2-4 unreachable -
    there is nothing behind it worth spending a radio connection on.

    Records 2-4 are read only when record 1 does *not* match, which is exactly the case where
    the device falls through to them: the phone app has overwritten record 1 with one of its
    windowed schedules, or record 1 is empty. Not asking, in that state, is what #27 was - Home
    Assistant read a truthful zero from record 1 and reported off while record 2 ran the
    atomiser. All three are then read rather than stopping at the first match, because one more
    query on an already-open link is cheaper than a second connect, and it makes the log a
    complete picture of the device instead of a prefix of one.

    A free function rather than a method because it needs nothing from the coordinator except
    the transport, and the coordinator cannot be constructed without a live ``hass`` - so this
    is the difference between the fall-through path being tested and being hoped about.
    """
    records = {1: record_1}
    if not record_1.matches_at(now):
        records.update(await device.async_query_records((2, 3, 4)))
    return protocol.DeviceState(records=records, at=now)


class IntensityMemory:
    """The work/pause pair Home Assistant intends to use, and where it came from.

    Its own object rather than two attributes on the coordinator, because it holds the one
    piece of state the diffuser genuinely cannot keep for us, and the precedence rules are
    easy to get wrong in a way that nothing notices until somebody presses "on":

    **off *is* ``work = 0``** (protocol.power), so a diffuser that is off is holding a record
    that no longer says what to resume at, and its next echo reads back ``work=0``. Absorb that
    as "the new intensity" and the following power-on dispenses for zero seconds - the diffuser
    appears to turn on and do nothing.

    Precedence, highest first:

    1. **what the device confirmed** — a 0xFB echo carrying non-zero work;
    2. **what Home Assistant restored** from its own storage at startup;
    3. **the protocol default**.

    Being free of Home Assistant also means these rules can be tested without a hass
    fixture - see tests/test_entities.py.
    """

    def __init__(self) -> None:
        # Both fields from the same preset. Taking pause from protocol.DEFAULT_PAUSE_S
        # instead would couple two constants that are equal today by coincidence, and the
        # day one moved the default state would match no preset at all - the select would
        # read "Custom" before the user had touched anything.
        work, pause = protocol.INTENSITY_PRESETS[protocol.DEFAULT_INTENSITY]
        self.work: int = work
        self.pause: int = pause
        #: True once a device echo has supplied real values. Restoring is refused afterwards.
        self.from_device: bool = False

    @property
    def preset_name(self) -> str | None:
        """The Kirri preset these values correspond to, or None if they are custom."""
        for name, work_pause in protocol.INTENSITY_PRESETS.items():
            if work_pause == (self.work, self.pause):
                return name
        return None

    def absorb(self, record: protocol.ScheduleRecord) -> bool:
        """Take values from a device echo, ignoring an off (``work = 0``) one. True if taken.

        The ``work`` test is what makes the off frame safe to echo straight back into here,
        and it predates #27 - the machinery was already the right shape for the fix.
        """
        if not record.work:
            return False
        self.work = record.work
        self.pause = record.pause
        self.from_device = True
        return True

    def restore(self, *, work: int | None = None, pause: int | None = None) -> bool:
        """Seed from Home Assistant's own storage. Never overrides the device. True if taken.

        Ordering matters and is not accidental: __init__.py refreshes the coordinator *before*
        forwarding the platforms, so if the diffuser was on at startup its real values are
        already in hand by the time an entity restores, and this correctly declines.
        """
        if self.from_device:
            return False
        if work is not None:
            self.work = work
        if pause is not None:
            self.pause = pause
        return True

    def select(self, work: int, pause: int) -> None:
        """Record a choice the device has not confirmed - it is off and cannot be told yet."""
        self.work = work
        self.pause = pause


class PollHealth:
    """How many polls have failed, in a row and in total.

    Two jobs, and the second is the reason this is a counter rather than a boolean:

    1. **Decide whether the entities may hold a stale reading.** ``may_hold_reading`` is what
       lets a single missed poll pass without taking everything ``unavailable`` for the full
       interval - #38, and ADR-012 for why that is honest here.
    2. **Be the measurement channel the first job destroys.** Every read-failure number this
       project has - 13 dropouts in 72 h, 4.5%, the interval that reconciles reads with
       writes in docs/lab/soak-2026-08.md - was *inferred from `unavailable` periods in the recorder*,
       because a poll leaves no other trace. Tolerating a miss erases that inference, and the
       box has no persistent Home Assistant log to fall back on (`/api/error_log` 404s). So
       ``total`` is exported as a diagnostic sensor (sensor.py) and the recorder keeps it.
       Without that, a verification soak would report a clean box because the failures had
       been hidden, not because they had stopped.

    Plain Python, like IntensityMemory and for the same reason: the coordinator cannot be
    constructed without a live ``hass``, so rules that live in an object are tested rather
    than hoped about.
    """

    def __init__(self, tolerated: int = TOLERATED_POLL_FAILURES) -> None:
        self.tolerated = tolerated
        #: Polls in a row that produced no reading, whether they failed or never ran.
        self.consecutive = 0
        #: Failures since the config entry loaded, counting only polls actually attempted.
        #: Never decreases except on a reload.
        self.total = 0
        self.last_error: str | None = None
        self.last_failure: datetime | None = None
        #: True once the device has gone off air with no successful poll since. Tolerance is
        #: void while this is set - see not_attempted.
        self._off_air = False

    def failed(self, error: str, *, at: datetime | None = None) -> None:
        """A poll was attempted and did not come back."""
        self.consecutive += 1
        self.total += 1
        self.last_error = error
        self.last_failure = at or dt_util.now()

    def not_attempted(self) -> None:
        """A poll never left the building, because the device is not advertising.

        **Voids tolerance outright rather than costing one of its lives.** A poll we missed
        and a poll we never made are different evidence: a miss says the link dropped a
        message, an absence says the device was somewhere we could not see it, and anything
        may have happened to it there. Spending a tolerance life instead would leave a short
        outage - anything under two poll intervals - still inside the budget, so the entity
        would flick back to its pre-outage reading the instant advertisements returned,
        seconds before the refresh that could confirm it. Showing a state we cannot support
        is the shape of #27, and it is not worth those few seconds.

        ``total`` is deliberately untouched: it answers how often our BLE path drops a poll
        it actually tried, and folding in every mains cut would inflate the measured fault
        rate by exactly the case ``device_present`` already reports, correctly and for free.
        """
        self.consecutive += 1
        self._off_air = True

    def succeeded(self) -> None:
        """A fresh reading, from a poll *or* a write - both prove the link works.

        Clearing ``_off_air`` here and not when advertisements return is the point: the
        device coming back tells us it is there, not what it did while it was gone.
        """
        self.consecutive = 0
        self._off_air = False

    @property
    def may_hold_reading(self) -> bool:
        """Whether the previous reading is still worth showing rather than going unavailable."""
        return not self._off_air and self.consecutive <= self.tolerated


class KirriBeaconCoordinator(DataUpdateCoordinator[protocol.DeviceState]):
    """Holds what the diffuser last told us, and whether it is still there."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device: KirriBeaconDevice,
        poll_interval: int = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {device.address}",
            update_interval=timedelta(seconds=poll_interval),
            config_entry=entry,
        )
        self.device = device
        self.address = device.address

        # Intensity has to be remembered here, not read from the device: off *is* work = 0,
        # so once the diffuser is off its own record can no longer tell us what to resume at.
        # See IntensityMemory for the precedence rules.
        self.intensity = IntensityMemory()

        # Counts failed polls, and decides whether one miss is worth going unavailable over.
        # See PollHealth - it is also the only trace a tolerated failure leaves anywhere.
        self.health = PollHealth()

        self._present = False
        self._unsubscribers: list = []
        # Latches the "the app has taken record 1 back" notice so a 15-minute poll cannot
        # turn a standing condition into 96 log lines a day. Cleared when record 1 wins again.
        self._fallthrough_logged = False

    # -- intensity ----------------------------------------------------------------------

    @property
    def work_s(self) -> int:
        """Dispense seconds the next power-on will carry."""
        return self.intensity.work

    @property
    def pause_s(self) -> int:
        """Pause seconds the next power-on will carry."""
        return self.intensity.pause

    @property
    def intensity_name(self) -> str | None:
        """The matching Kirri preset name, or None if the values are custom."""
        return self.intensity.preset_name

    @callback
    def async_restore_intensity(
        self, *, work: int | None = None, pause: int | None = None
    ) -> None:
        """Seed intensity from an entity's restored state. Writes nothing to the device."""
        if self.intensity.restore(work=work, pause=pause):
            _LOGGER.debug(
                "%s: restored intensity %ss/%ss from Home Assistant storage",
                self.address, self.intensity.work, self.intensity.pause,
            )
            self.async_update_listeners()

    # -- presence ---------------------------------------------------------------------

    @property
    def device_present(self) -> bool:
        """Whether Home Assistant currently considers the diffuser to be on air.

        Ask Home Assistant rather than tracking advertisements ourselves. That is not a
        style preference - an earlier version of this counted the time since the last
        advertisement callback, and it was **wrong in a way that looked exactly like a dead
        diffuser**:

        habluetooth's dispatch returns early when an advertisement is byte-identical to the
        previous one, *before* invoking registered callbacks. The Beacon's advertisement
        never varies - flags 05, uuid16-partial f0 ff, no manufacturer data, no name, no
        service data (docs/research/dossier.md 6.1) - so after the first few packets the
        callback stopped firing permanently, and the entity went unavailable while the
        device sat there advertising happily.

        async_address_present reads the manager's history directly and has no such gap.
        """
        return bluetooth.async_address_present(self.hass, self.address, connectable=True)

    @callback
    def async_start_presence_tracking(self) -> None:
        """Begin watching for the diffuser coming and going. Call once, from setup."""
        self._present = self.device_present

        # Going away: Home Assistant's own tracker, which knows the device's advertising
        # interval and applies the right grace period. Better than any timeout we'd pick.
        self._unsubscribers.append(
            bluetooth.async_track_unavailable(
                self.hass, self._async_on_unavailable, self.address, connectable=True
            )
        )
        # Coming back: this callback *is* reliable for the recovery edge specifically. The
        # early-return described above is skipped when a connectable device is missing from
        # connectable history - which is exactly the state it is in after being marked
        # unavailable. So the one edge we most need (a diffuser switched back on at its
        # base after a power cut, #8) is the one that still fires.
        self._unsubscribers.append(
            bluetooth.async_register_callback(
                self.hass,
                self._async_on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )

    @callback
    def _async_on_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        self._async_evaluate_presence()

    @callback
    def _async_on_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        self._async_evaluate_presence()

    @callback
    def _async_evaluate_presence(self) -> None:
        present = self.device_present
        if present == self._present:
            return
        self._present = present

        if present:
            _LOGGER.info("%s: advertising again", self.address)
            # It has just come back - almost certainly from a power cut, which means someone
            # pressed the button on its base and the state we hold is stale. This is the
            # moment a refresh is actually worth a radio connection.
            self.hass.async_create_task(self.async_request_refresh())
        else:
            _LOGGER.info(
                "%s: stopped advertising - if this follows a power cut the diffuser needs "
                "its base button pressed by hand (docs/hardware.md)",
                self.address,
            )
        self.async_update_listeners()

    @callback
    def async_shutdown_tracking(self) -> None:
        while self._unsubscribers:
            self._unsubscribers.pop()()

    # -- state ------------------------------------------------------------------------

    async def _async_update_data(self) -> protocol.DeviceState:
        if not self.device_present:
            # Deliberately cheap: no connection attempt, no radio, no stack trace. The
            # coordinator will simply try again next interval, forever, which is exactly the
            # reconnect policy #8 calls for - there is no bounded retry to exhaust.
            self.health.not_attempted()
            raise UpdateFailed(f"{self.address} is not advertising")
        try:
            record_1 = await self.device.async_query(1)
            state = await self._async_resolve(record_1)
        except KirriError as err:
            # Still raised, still logged, still flips last_update_success - #38 changes what
            # the *entities* expose, not what the coordinator records. See entity.available.
            self.health.failed(str(err))
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # re-raised immediately
            # Anything that is not a KirriError is a bug rather than a flaky link, and the
            # coordinator will report it as an unexpected error either way. It still has to
            # break the streak: a failure the counter never sees would leave every entity
            # available on a frozen reading *indefinitely*, which is the one outcome worse
            # than going unavailable. Fail-safe rather than fail-quiet.
            self.health.failed(f"{type(err).__name__}: {err}")
            raise
        self.health.succeeded()
        # Intensity comes from record 1 specifically, never from a record we fell through to:
        # record 1 is the one Home Assistant writes, so it is the only one whose work/pause
        # describe what *our* next power-on will carry. An app record's timings are the app's.
        self.intensity.absorb(record_1)
        return state

    async def _async_resolve(
        self, record_1: protocol.ScheduleRecord
    ) -> protocol.DeviceState:
        """resolve_state() against the clock, plus the "this is not ours" notice."""
        state = await resolve_state(self.device, record_1, dt_util.now())
        self._log_arbitration(state)
        return state

    def _log_arbitration(self, state: protocol.DeviceState) -> None:
        """Say so, once, when the device is running a record Home Assistant did not write."""
        active = state.active
        if active is not None and active.record == 1:
            self._fallthrough_logged = False
            return
        _LOGGER.debug(
            "%s: record 1 does not match; device holds %s",
            self.address,
            " | ".join(
                f"{rec.record}: days={protocol.describe_days(rec.days)} "
                f"{rec.start[0]:02d}:{rec.start[1]:02d}-{rec.end[0]:02d}:{rec.end[1]:02d} "
                f"work={rec.work}s"
                for rec in sorted(state.records.values(), key=lambda r: r.record)
            ),
        )
        if self._fallthrough_logged:
            return
        self._fallthrough_logged = True
        if active is None:
            _LOGGER.info(
                "%s: record 1 is not Home Assistant's and no record matches right now, so the "
                "diffuser is idle. Turning it on or off from Home Assistant reclaims record 1.",
                self.address,
            )
        else:
            _LOGGER.info(
                "%s: record 1 is not Home Assistant's, so the device is running record %d "
                "(%s, %02d:%02d-%02d:%02d, %ss on / %ss off) - the phone app has taken record 1 "
                "back. Turning the diffuser on or off from Home Assistant reclaims it.",
                self.address, active.record, protocol.describe_days(active.days),
                active.start[0], active.start[1], active.end[0], active.end[1],
                active.work, active.pause,
            )

    # -- commands ---------------------------------------------------------------------

    async def async_set_power(self, on: bool) -> protocol.DeviceState:
        """Turn the diffuser on (at the remembered intensity) or off.

        Both directions write an all-day record 1, so both take ownership of it back from the
        phone app. Off is not a cleared record - see protocol.power() and #27.
        """
        frame = protocol.power(on, work=self.work_s, pause=self.pause_s)
        return await self._async_write(frame)

    async def async_set_intensity(self, work: int, pause: int) -> protocol.DeviceState | None:
        """Change the work/pause values.

        **Only writes if the diffuser is already on.** Every intensity frame is also a
        power-on frame - there is no way to express "set intensity" without a day mask - so
        writing one while the device is off would silently turn it on. When off, the values
        are remembered and applied by the next power-on.

        When it *is* on, the remembered values are updated from the echo rather than from the
        request, which means a write that fails leaves the previous intensity intact and the
        entity snaps back to it. Optimistically storing the requested value first would leave
        Home Assistant confidently displaying an intensity the diffuser never accepted.

        Note "already on" now means *the device* is dispensing, not "record 1 has days set" -
        so if the diffuser is running one of the phone app's schedules, changing the intensity
        writes an all-day record 1 and takes ownership. The device keeps running, at the
        requested intensity, from then on rather than only inside the app's window. That is the
        intended trade: the alternative is silently declining to apply a change the user made
        while watching the thing mist.
        """
        if self.data is None or not self.data.is_on:
            self.intensity.select(work, pause)
            _LOGGER.debug(
                "%s: diffuser is off, holding intensity %ss/%ss until it is turned on",
                self.address, work, pause,
            )
            self.async_update_listeners()
            return None
        return await self._async_write(protocol.power(True, work=work, pause=pause))

    async def async_sync_clock(self) -> None:
        """Push Home Assistant's wall clock to the diffuser's RTC.

        Affects nothing this integration reads: the power path uses an all-day window where
        the RTC is never consulted, and on-device schedules were dropped (#17, ADR-009).
        **Unconfirmable** - the device never acknowledges this frame - so returning here
        means the write was accepted, not that the clock was set.
        """
        await self.device.async_sync_clock()

    async def _async_write(self, frame: bytes) -> protocol.DeviceState:
        record = await self.device.async_write_schedule(frame)
        # An echo proves the link works exactly as well as a poll does, so it clears the
        # streak too. Without this a command the user just watched succeed would leave the
        # entities one failed poll from unavailable, on evidence that is already stale.
        self.health.succeeded()
        self.intensity.absorb(record)
        # No extra radio here in practice: we only ever write an all-day record 1, so it
        # matches, wins arbitration, and _async_resolve stops without touching records 2-4.
        # Going through the same resolver as the poll is what guarantees that - the invariant
        # is checked rather than assumed, and if it were ever broken the state would still be
        # right, only slower.
        try:
            state = await self._async_resolve(record)
        except KirriError as err:
            # Only reachable if that invariant is ever broken - a writer that sends a record
            # with a narrow window would make the resolver go and read records 2-4, and that
            # read can fail. The write itself is already confirmed by its echo, and a failed
            # follow-up read does not un-write it: raising here would tell the user their
            # command did not land when it did. Report what the echo alone can support.
            _LOGGER.debug(
                "%s: write confirmed, but reading the rest of the table failed: %s",
                self.address, err,
            )
            state = protocol.DeviceState(records={record.record: record}, at=dt_util.now())
        # The echo is authoritative and we already have it, so push it straight out rather
        # than making the UI wait for the next poll.
        self.async_set_updated_data(state)
        return state
