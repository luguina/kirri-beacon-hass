"""BLE transport for the Kirri Beacon.

Owns the link and nothing else: connect on demand, run the documented command sequence,
confirm writes by their echo, drop the link. All framing lives in protocol.py; all state
lives in the coordinator.

Four things here are consequences of measurements rather than of taste, and are commented
where they appear:

* **a write returning proves nothing** — the device accepts a 0-byte write at the ATT layer
  (dossier 6.5), so a command has succeeded only when its ``A5FB`` echo comes back matching;
* **the clock sync is never acknowledged** (verified 2026-08-09) — waiting for an echo after
  it would stall every connect for the full timeout;
* **a missing echo is absent, not late** (measured 2026-08-13) — so it is retried rather than
  waited out, and the retry is counted because a working retry and a rare fault look identical
  from the outside (#35, ADR-013);
* **"not reachable" is a normal state**, not a fault: after a mains cut the diffuser stays
  off until somebody presses the button on its base (#8).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    CLOCK_SYNC_INTERVAL_S,
    CONNECT_TIMEOUT_S,
    DEFAULT_DRAIN_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    ECHO_TIMEOUT_S,
    NOTIFY_UUID,
    SEQUENCE_DELAY_S,
    SERVICE_UUID,
    WRITE_UUID,
)

_LOGGER = logging.getLogger(__name__)


class KirriError(Exception):
    """Base class for anything this transport can fail with."""


class KirriNotFound(KirriError):
    """The diffuser is not advertising.

    Expected, not exceptional: after a power cut it stays off until somebody presses the
    button on its base. Callers should report unavailable and keep retrying.
    """


class KirriNotSupported(KirriError):
    """Connected, but the vendor service is absent - this is some other FFF0 device."""


class KirriCommandFailed(KirriError):
    """The write went out but the device did not confirm it."""


class KirriEchoTimeout(KirriCommandFailed):
    """The device answered nothing at all within ``ECHO_TIMEOUT_S``.

    Split from its parent because the two ways a command can go unconfirmed want opposite
    treatment, and lumping them together is what #35 was:

    * **nothing came back** — this. The device ignored us, and a fresh connect-and-resend is
      the right response. Measured *absent* rather than late on 2026-08-13: 58 of 58 echoes
      arrived within 0.11 s of a 5.0 s deadline, so waiting longer catches nothing;
    * **the wrong thing came back** — a plain ``KirriCommandFailed``. The device disagreed
      with us rather than ignored us, and resending the same frame is likely to reproduce the
      disagreement while hiding a protocol bug worth seeing.

    A subclass rather than a sibling so that every ``except KirriCommandFailed`` written
    before this existed keeps catching it.
    """


#: What is worth a second attempt. Echo *mismatch* is deliberately absent - see
#: KirriEchoTimeout for why the two unconfirmed-write cases are not the same case.
_RETRYABLE = (BleakError, asyncio.TimeoutError, KirriEchoTimeout)


class CommandHealth:
    """How often the link needed a second attempt, and whether it saved the command.

    The meter that ships with #35's fix, for the same reason ADR-012 made one part of #38:
    **the fix destroys the evidence it should be judged on.** Before it, every missing echo
    surfaced as a failed command - a write error the soak harness logged, or a poll failure
    that took the entity ``unavailable``. After it, a rescued command is indistinguishable
    from one that never had a problem, so the same fault rate reads as a clean box. The
    diffuser box has no persistent Home Assistant log to fall back on (``/api/error_log``
    404s), so a counter that is not an entity is not readable at all.

    It also measures something no existing instrument does. ``PollHealth`` counts *poll*
    failures; this sits one layer down in the transport, so it sees reads and writes alike -
    which is exactly the comparison ``docs/lab/soak-2026-08.md`` had to make with a derived
    denominator and two overlapping confidence intervals.

    ``echo_timeouts`` is the one to trust over time. The retry counters describe *our retry
    policy* and change meaning if the policy ever changes; ``echo_timeouts`` describes the
    device and the link, and will still mean the same thing in a year. That is why it, and
    not the retry count, is the value exported to long-term statistics (sensor.py).

    The retry counters are **scoped by cause**, which is not fussiness: _async_run retries a
    stale link as well as a missing echo, and counting both into one pair would let a
    stale-link retry report itself as a rescued echo. A window with no missing echoes at all
    could then read "0 missing echoes, 3 rescued by the retry" - the instrument telling a
    story about a fault that did not occur, which is the exact failure this class exists to
    prevent.

    Plain Python with no Home Assistant in it, like ``IntensityMemory`` and ``PollHealth``,
    so the rules are tested rather than hoped about.
    """

    def __init__(self) -> None:
        #: Attempts whose A5FB echo never arrived, counted where it happens rather than where
        #: it surfaces - so a retry that rescues the command still leaves the fault on record.
        #: Spans both attempts, and both reads and writes.
        self.echo_timeouts = 0
        #: ...of which were writes. The read count is the remainder, so the two cannot drift
        #: apart. This is the split docs/lab/soak-2026-08.md had to infer: it argued reads and writes
        #: are one defect from a *derived* poll denominator and two overlapping confidence
        #: intervals, because nothing counted the two populations directly. Now something does,
        #: and a re-soak can confirm or refute it rather than reasoning around it.
        self.write_echo_timeouts = 0
        #: Commands retried **because of a missing echo** specifically. Scoped to that cause
        #: rather than counting every retry, because the whole point of these three is to say
        #: what happened to #35's fault - and _async_run also retries a stale link, which is a
        #: different, older fault. Mixing them lets a stale-link retry report itself as a
        #: rescued echo, so a window with no missing echoes at all could read "0 missing
        #: echoes, 3 rescued by the retry".
        self.echo_retries = 0
        #: ...of which the second attempt then succeeded. The fix working, counted.
        self.echo_recovered = 0
        #: ...of which the second attempt failed too, so the caller got an error.
        #: ``echo_retries == echo_recovered + echo_failures``, always.
        self.echo_failures = 0
        #: Retries driven by anything else - a stale link, a transport timeout. Counted so
        #: they are not invisible, but not broken down by outcome: that fault predates this
        #: work, was never instrumented before it, and still surfaces on its own terms (a
        #: failed poll reaches sensor.…_failed_polls, a failed command reaches the caller).
        #: Splitting it further would be measuring something nobody is currently asking about.
        self.other_retries = 0
        #: The most recent retry of *either* kind, so a single sample still says what drove it.
        self.last_retry_error: str | None = None
        self.last_retry: datetime.datetime | None = None

    @property
    def read_echo_timeouts(self) -> int:
        """The other half of the split. Derived, so it can never disagree with the total."""
        return self.echo_timeouts - self.write_echo_timeouts

    def echo_timed_out(self, *, write: bool) -> None:
        """An attempt reached the echo deadline with nothing to show for it."""
        self.echo_timeouts += 1
        if write:
            self.write_echo_timeouts += 1

    def retrying(
        self, error: str, *, echo: bool, at: datetime.datetime | None = None
    ) -> None:
        """A first attempt failed on something worth trying again.

        ``echo`` says which fault drove it, and every later call about this command must be
        given the same answer - see _async_run, which carries it in a local.
        """
        if echo:
            self.echo_retries += 1
        else:
            self.other_retries += 1
        self.last_retry_error = error
        self.last_retry = at or dt_util.now()

    def rescued(self, *, echo: bool) -> None:
        """A second attempt succeeded. For a missing echo, the number #35 is judged on."""
        if echo:
            self.echo_recovered += 1

    def exhausted(self, *, echo: bool) -> None:
        """Both attempts failed, and the caller is about to hear about it."""
        if echo:
            self.echo_failures += 1


class KirriBeaconDevice:
    """Connect-on-demand BLE link to one diffuser."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        drain_seconds: float = DEFAULT_DRAIN_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.hass = hass
        # Normalise once, here, so every consumer downstream gets the same form. Home
        # Assistant's bluetooth manager keys its history on the upper-case address; a
        # lower-case one makes async_address_present() report the device permanently absent
        # while connections still succeed, which is a nasty split to debug.
        self.address = address.upper()
        self.drain_seconds = drain_seconds
        self.max_attempts = max_attempts

        self._lock = asyncio.Lock()
        self._client: BleakClientWithServiceCache | None = None
        self._notifications: asyncio.Queue[bytes] = asyncio.Queue()
        # None, not 0.0: time.monotonic() is measured from an arbitrary epoch, so a 0.0
        # sentinel would look "recent" on a host that booted minutes ago and would skip the
        # very first sync.
        self._last_clock_sync: float | None = None
        self._disconnect_task: asyncio.Task[None] | None = None
        self._live_status_seen = False

        # Counts missing echoes and what the retry did about them. Lives on the transport
        # rather than the coordinator because that is where both reads and writes pass; see
        # CommandHealth, and sensor.py for how it gets somewhere a human can read it.
        self.health = CommandHealth()

    # -- link management -------------------------------------------------------------

    def _ble_device(self) -> BLEDevice:
        """Resolve the address through Home Assistant's bluetooth stack.

        This call is what routes over the ESPHome proxy. Using BleakScanner directly would
        only ever see adapters attached to the HA box, which is three floors away.
        """
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise KirriNotFound(
                f"{self.address} is not advertising - the diffuser is probably switched off "
                "at its base (see docs/hardware.md, power-cycle behaviour)"
            )
        return ble_device

    def _on_disconnected(self, _client: BleakClientWithServiceCache) -> None:
        # Fires for links the device drops as well as ones we close, so it must not assume
        # a teardown is in progress.
        _LOGGER.debug("%s: link closed", self.address)
        self._client = None

    def _handle_notification(self, _sender: Any, data: bytearray) -> None:
        pkt = bytes(data)
        _LOGGER.debug("%s: RX %s", self.address, pkt.hex().upper())

        if protocol.is_live_status(pkt) and not self._live_status_seen:
            # Worth shouting about: this frame is documented from the vendor's parser but
            # has never once been seen on air (16/16 notifications to date were echoes).
            # If it ever does arrive, the evidence should not be buried at debug level.
            self._live_status_seen = True
            _LOGGER.info(
                "%s: FIRST EVER 21-byte live-status frame: %s - please report this on "
                "issue #13, it unblocks the work/pause remaining sensors",
                self.address,
                pkt.hex().upper(),
            )

        self._notifications.put_nowait(pkt)

    async def _async_connect(self) -> BleakClientWithServiceCache:
        """Open the link and run the vendor's documented initialisation sequence."""
        if self._client is not None and self._client.is_connected:
            return self._client

        ble_device = self._ble_device()
        _LOGGER.debug("%s: connecting", self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.address,
            self._on_disconnected,
            use_services_cache=True,
            max_attempts=self.max_attempts,
            timeout=CONNECT_TIMEOUT_S,
        )

        if client.services.get_service(SERVICE_UUID) is None:
            # FFF0 is all this device puts on air and it is shared with a lot of cheap
            # hardware, so the vendor service is the only real proof of identity.
            await client.disconnect()
            raise KirriNotSupported(
                f"{self.address} has no {SERVICE_UUID} service - not a Kirri Beacon"
            )

        self._client = client

        # docs/protocol.md: subscribe, settle, clock sync, settle, then commands.
        await client.start_notify(NOTIFY_UUID, self._handle_notification)
        await asyncio.sleep(SEQUENCE_DELAY_S)
        await self._async_maybe_clock_sync(client)
        await asyncio.sleep(SEQUENCE_DELAY_S)
        return client

    async def _async_maybe_clock_sync(self, client: BleakClientWithServiceCache) -> None:
        """Send the clock sync, at most once an hour.

        Fire-and-forget by necessity: verified on the device 2026-08-09, this frame draws no
        notification at all. There is nothing to wait for, and waiting would cost the full
        echo timeout on every connect.

        Nothing downstream depends on the result: the power path uses an all-day/all-days
        window where the RTC is never consulted, and on-device schedules were dropped (#17,
        ADR-009). Hourly is generous rather than a compromise.
        """
        now = time.monotonic()
        if self._last_clock_sync is not None and now - self._last_clock_sync < CLOCK_SYNC_INTERVAL_S:
            return
        frame = protocol.clock_sync_now(_local_now())
        _LOGGER.debug("%s: TX clock sync %s", self.address, frame.hex().upper())
        await client.write_gatt_char(WRITE_UUID, frame, response=True)
        self._last_clock_sync = now

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            if client.is_connected:
                # Unsubscribe before dropping the link: some firmwares in this family wedge
                # if a subscribed client just disappears.
                try:
                    await client.stop_notify(NOTIFY_UUID)
                except BleakError as err:
                    _LOGGER.debug("%s: stop_notify failed, continuing: %s", self.address, err)
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("%s: disconnect failed, dropping the handle anyway: %s", self.address, err)

    def _schedule_disconnect(self) -> None:
        """Close the link after the drain window, unless another command arrives first."""
        self._cancel_disconnect()
        self._disconnect_task = self.hass.async_create_task(
            self._async_drain_then_close(max(0.0, self.drain_seconds))
        )

    def _cancel_disconnect(self) -> None:
        if self._disconnect_task is not None and not self._disconnect_task.done():
            self._disconnect_task.cancel()
        self._disconnect_task = None

    async def _async_drain_then_close(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        # Take the lock so this can never race a command that started during the sleep.
        async with self._lock:
            await self._async_disconnect()

    async def async_shutdown(self) -> None:
        """Close everything. Safe to call when already disconnected."""
        self._cancel_disconnect()
        async with self._lock:
            await self._async_disconnect()

    # -- command execution -----------------------------------------------------------

    async def _async_run[T](
        self, action: Callable[[BleakClientWithServiceCache], Awaitable[T]]
    ) -> T:
        """Run one exchange, retrying it once.

        Three faults share this retry, and they are not the same fault:

        * **a stale link** — a cached-but-dead connection is indistinguishable from a live one
          until the first write. This is what the retry was originally written for, and it is
          the *only* thing it used to catch;
        * **a missing echo** — the device answered nothing within ``ECHO_TIMEOUT_S``. It was
          not caught, so a write whose echo went astray failed on its first and only attempt:
          three of 22 soak exercises and an inferred 4.5% of polls, which is #35. Echoes are
          absent rather than late (docs/lab/soak-2026-08.md), so a fresh connect-and-resend is the one
          thing left to change;
        * **a transport timeout** raised by bleak itself.

        An **echo mismatch stays out** — see KirriEchoTimeout. It is the case where the device
        answered, just not with what we sent.

        Still exactly one retry. Giving up and letting the coordinator try again on its next
        cycle is what implements "retry forever, report unavailable" (#8), and a loop here
        would hold both this lock and the proxy's single connection slot while it ran.

        **Retrying a write is safe because every frame this transport sends is absolute, not
        incremental**: a schedule record is set to a value, never adjusted by one. So the
        awkward case — the command did land and only its echo went missing — resends the same
        record with the same contents, and the second write is a no-op at the device.

        The cost is paid only by commands that were going to fail anyway: a failure now takes
        two connects plus two echo timeouts, **~14-20 s** at the measured connect range
        (0.9-4.1 s) against ~7 s before, bounded above by 2 x (CONNECT_TIMEOUT_S +
        ECHO_TIMEOUT_S). That is inside the soak harness's 45 s per leg, and its 300 s
        per-trigger deadline was already sized for this doubling. Successful commands are
        untouched - nothing here runs unless the first attempt has already failed.
        """
        async with self._lock:
            self._cancel_disconnect()
            # Which fault sent us round again. Attempt 2's outcome has to be filed against
            # *that*, not against whatever attempt 2 itself failed with - an echo timeout
            # followed by a BleakError is still an echo-driven retry that did not work.
            retried_on_echo = False
            try:
                for attempt in (1, 2):
                    try:
                        client = await self._async_connect()
                        result = await action(client)
                    except _RETRYABLE as err:
                        await self._async_disconnect()
                        if attempt == 2:
                            self.health.exhausted(echo=retried_on_echo)
                            if isinstance(err, KirriError):
                                # Already ours, and already says what failed and at which
                                # address. Wrapping it again would only repeat the address
                                # and bury the specific type callers may want to match on.
                                raise
                            raise KirriCommandFailed(
                                f"{self.address}: {type(err).__name__}: {err}"
                            ) from err
                        retried_on_echo = isinstance(err, KirriEchoTimeout)
                        self.health.retrying(
                            f"{type(err).__name__}: {err}", echo=retried_on_echo
                        )
                        _LOGGER.debug(
                            "%s: attempt %d failed (%s), reconnecting", self.address, attempt, err
                        )
                    else:
                        if attempt == 2:
                            self.health.rescued(echo=retried_on_echo)
                        return result
                raise AssertionError("unreachable")
            finally:
                self._schedule_disconnect()

    async def _async_await_echo(
        self, expected: bytes | None, want_record: int | None = None
    ) -> protocol.ScheduleRecord:
        """Wait for the 0xFB echo.

        ``expected`` is the exact frame we require (a write), or None to accept whatever
        record comes back (a query). Non-echo notifications are skipped rather than treated
        as failures, so an unsolicited live-status frame can never break a command.

        ``want_record`` filters by record id instead, for a query whose answer must be
        attributed to the record that was asked for. It matters only when several queries share
        one link (async_query_records): without it, a reply that arrived a moment late would be
        handed to the *next* query and silently filed under the wrong record - the kind of
        off-by-one that reads as a device inconsistency rather than as a bug here.

        The two ways this fails raise **different** exceptions, and the difference is the whole
        of #35: silence is a ``KirriEchoTimeout`` and _async_run tries again, a mismatch is a
        plain ``KirriCommandFailed`` and it does not.
        """
        deadline = time.monotonic() + ECHO_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Counted here, at the fault, rather than where it surfaces: _async_run may
                # well rescue this command, and a rescued fault that leaves no trace is the
                # instrument-breaking-the-measurement problem ADR-012 named. See CommandHealth.
                #
                # `expected is not None` *is* the read/write split, not a proxy for it: only a
                # write knows the frame it requires back, because it sent it. A query cannot
                # know the answer in advance, which is why it passes None and matches on the
                # record id instead. The equivalence is structural, so it cannot drift.
                self.health.echo_timed_out(write=expected is not None)
                raise KirriEchoTimeout(
                    # :g rather than :.0f - the latter renders any sub-second timeout as "0s",
                    # which is what the tests patch it to and what a future tuning could set.
                    # 5.0 still reads "5s"; 0.2 now reads "0.2s" instead of lying.
                    f"{self.address}: no matching A5FB echo within {ECHO_TIMEOUT_S:g}s - "
                    "the write was accepted at the ATT layer but the device did not confirm it"
                )
            try:
                pkt = await asyncio.wait_for(self._notifications.get(), remaining)
            except asyncio.TimeoutError:
                continue
            if not protocol.is_echo(pkt):
                _LOGGER.debug("%s: ignoring non-echo notification %s", self.address, pkt.hex().upper())
                continue
            if expected is not None and pkt != expected:
                # Deliberately the base class, so _RETRYABLE does not match it: the device
                # answered, it just disagreed. Never once observed on air.
                raise KirriCommandFailed(
                    f"{self.address}: echo mismatch - sent a record the device answered with "
                    f"{pkt.hex().upper()}, expected {expected.hex().upper()}"
                )
            record = protocol.decode_echo(pkt)
            if want_record is not None and record.record != want_record:
                # Stale rather than wrong: skip it and keep waiting inside the same deadline.
                _LOGGER.debug(
                    "%s: ignoring echo for record %d while waiting for record %d",
                    self.address, record.record, want_record,
                )
                continue
            return record

    def _drain_stale_notifications(self) -> None:
        """Empty the queue so a leftover frame cannot satisfy the next command."""
        while not self._notifications.empty():
            stale = self._notifications.get_nowait()
            _LOGGER.debug("%s: discarding stale notification %s", self.address, stale.hex().upper())

    async def async_write_schedule(self, frame: bytes) -> protocol.ScheduleRecord:
        """Send a 0xFA record and return it only once the device echoes it back."""
        expected = protocol.echo_for(frame)

        async def action(client: BleakClientWithServiceCache) -> protocol.ScheduleRecord:
            self._drain_stale_notifications()
            _LOGGER.debug("%s: TX %s", self.address, frame.hex().upper())
            await client.write_gatt_char(WRITE_UUID, frame, response=True)
            return await self._async_await_echo(expected)

        return await self._async_run(action)

    async def async_sync_clock(self) -> None:
        """Send the clock sync now, ignoring the once-an-hour rate limit.

        **Nothing here can confirm it.** This is the one frame the device never acknowledges
        (verified 2026-08-09), so returning means the write was accepted at the ATT layer -
        which on this device is explicitly not proof of anything. Raising when the *connection*
        fails is the only honest signal available, and that is what this gives.

        Clearing the timestamp before connecting rather than after is what stops the frame
        going out twice: on a fresh link the connect sequence sends it and the action below
        then finds the limit unexpired; on a link still open inside the drain window the
        connect returns early and the action sends it instead. Exactly one, either way.
        """
        self._last_clock_sync = None

        async def action(client: BleakClientWithServiceCache) -> None:
            await self._async_maybe_clock_sync(client)

        await self._async_run(action)

    async def async_query(self, record: int = 1) -> protocol.ScheduleRecord:
        """Ask the device what a record currently holds."""
        frame = protocol.status_query(record)

        async def action(client: BleakClientWithServiceCache) -> protocol.ScheduleRecord:
            self._drain_stale_notifications()
            _LOGGER.debug("%s: TX %s", self.address, frame.hex().upper())
            await client.write_gatt_char(WRITE_UUID, frame, response=True)
            return await self._async_await_echo(None)

        return await self._async_run(action)

    async def async_query_records(
        self, records: Sequence[int]
    ) -> dict[int, protocol.ScheduleRecord]:
        """Read several records over a **single** link, in the order given.

        One connect for the batch rather than one per record, because a connect costs 1.5-5 s
        against this device while a query costs a few hundred milliseconds - the link is the
        expensive part, not the traffic on it.

        Used only when record 1 does not settle the device's state on its own, so this is the
        fall-through path (coordinator._async_resolve) and not the common one. Non-mutating,
        which is what makes the retry in _async_run safe here: a batch that fails partway is
        simply run again from the start.
        """
        frames = [(record, protocol.status_query(record)) for record in records]

        async def action(
            client: BleakClientWithServiceCache,
        ) -> dict[int, protocol.ScheduleRecord]:
            answers: dict[int, protocol.ScheduleRecord] = {}
            for record, frame in frames:
                self._drain_stale_notifications()
                _LOGGER.debug("%s: TX %s", self.address, frame.hex().upper())
                await client.write_gatt_char(WRITE_UUID, frame, response=True)
                answers[record] = await self._async_await_echo(None, want_record=record)
            return answers

        return await self._async_run(action)


def _local_now() -> datetime.datetime:
    """Local wall-clock time, per Home Assistant's configured timezone.

    dt_util.now() rather than datetime.now(): the latter follows the *host* timezone, which
    Home Assistant does not set from its own configuration. They often agree - on the machine
    this was developed against both were AEST - but on a container running TZ=UTC with Home
    Assistant configured elsewhere, the device would be given UTC instead of local time.

    Since on-device schedules were dropped (#17, ADR-009), no schedule of ours can be broken
    by getting this wrong. Sending the right wall clock costs the same as sending a wrong one
    though, and it keeps the option open.

    The device has no timezone concept; it just holds a wall clock.
    """
    return dt_util.now()
