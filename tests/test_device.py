"""Tests for the BLE transport's write-confirmation discipline.

The single most important rule in this integration is that **a write is not a success until
its A5FB echo comes back matching** — on this device an ATT-layer accept proves nothing, a
0-byte write is accepted too (docs/research/dossier.md 6.5). These tests hold that rule down
with a fake GATT client, so it cannot regress into optimistic writes.

The fake also encodes a measured fact: the clock sync draws **no** notification at all
(verified on the device 2026-08-09). A transport that waits for one would hang here, which is
the point — it hung nowhere else until it was too late to notice.

Needs Home Assistant importable, because device.py legitimately depends on it. Skipped
otherwise, so `python3 -m pytest` still works on a bare machine for the protocol suite.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant", reason="transport tests need Home Assistant installed")

import protocol
from custom_components.kirri_beacon import device as device_mod
from custom_components.kirri_beacon.device import (
    KirriBeaconDevice,
    KirriCommandFailed,
    KirriEchoTimeout,
    KirriNotFound,
    KirriNotSupported,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"  # deliberately not the real one


class FakeServices:
    def __init__(self, present: bool = True) -> None:
        self._present = present

    def get_service(self, _uuid):
        return object() if self._present else None


class FakeClient:
    """Minimal stand-in for BleakClientWithServiceCache.

    ``responder`` maps an outbound frame to the notification the device would send back, or
    None for silence.
    """

    def __init__(self, responder, has_service: bool = True) -> None:
        self._responder = responder
        self._notify_cb = None
        self.writes: list[bytes] = []
        self.is_connected = True
        self.services = FakeServices(has_service)
        self.stop_notify_calls = 0

    async def start_notify(self, _uuid, callback) -> None:
        self._notify_cb = callback

    async def stop_notify(self, _uuid) -> None:
        self.stop_notify_calls += 1

    async def write_gatt_char(self, _uuid, data, response: bool = True) -> None:
        frame = bytes(data)
        self.writes.append(frame)
        reply = self._responder(frame)
        if reply is not None and self._notify_cb is not None:
            self._notify_cb(None, bytearray(reply))

    async def disconnect(self) -> None:
        self.is_connected = False


class FakeHass:
    """Just enough hass for the transport: it only ever creates tasks."""

    def async_create_task(self, coro, *_args, **_kwargs):
        return asyncio.get_running_loop().create_task(coro)


def _install(monkeypatch, client: FakeClient | None, *, advertising: bool = True) -> None:
    monkeypatch.setattr(
        device_mod.bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable=True: object() if advertising else None,
    )

    async def _establish(*_args, **_kwargs):
        return client

    monkeypatch.setattr(device_mod, "establish_connection", _establish)


def _install_per_connect(monkeypatch, responder_for, *, advertising: bool = True) -> list:
    """Like _install, but hands out a **new** FakeClient for every connect.

    The retry's whole claim is that it opens a fresh link rather than resending down the one
    that just failed, and a single shared client cannot tell those apart. ``responder_for``
    takes the connect index (0 for the first) and returns that link's responder; the returned
    list grows as links are opened, so a test can assert how many there were.
    """
    clients: list[FakeClient] = []

    monkeypatch.setattr(
        device_mod.bluetooth,
        "async_ble_device_from_address",
        lambda hass, address, connectable=True: object() if advertising else None,
    )

    async def _establish(*_args, **_kwargs):
        client = FakeClient(responder_for(len(clients)))
        clients.append(client)
        return client

    monkeypatch.setattr(device_mod, "establish_connection", _establish)
    return clients


def silent(_frame: bytes) -> None:
    """A diffuser that accepts everything at the ATT layer and confirms nothing."""
    return None


def echo_responder(frame: bytes) -> bytes | None:
    """A well-behaved diffuser: echoes schedule writes and queries, ignores clock syncs."""
    if frame[1] == protocol.CMD_CLOCK:
        return None  # measured 2026-08-09: never acknowledged
    if frame[1] == protocol.CMD_SCHEDULE:
        return protocol.echo_for(frame)
    if frame[1] == protocol.CMD_QUERY:
        return protocol.echo_for(protocol.power(True))
    return None


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# The happy path, and the connect sequence
# ---------------------------------------------------------------------------


def test_a_confirmed_write_returns_the_echoed_record(monkeypatch):
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            return await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    record = run(scenario())
    assert record.dispenses
    assert record.work == 6
    assert record.intensity_name == "Delicate"


def test_the_clock_sync_goes_first_and_is_not_waited_on(monkeypatch):
    # If this ever regresses into awaiting an echo, the test hangs rather than fails —
    # which is exactly what would happen to every command in production.
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    run(scenario())
    assert client.writes[0][1] == protocol.CMD_CLOCK
    assert client.writes[1][1] == protocol.CMD_SCHEDULE


def test_the_clock_sync_is_not_repeated_on_a_reused_link(monkeypatch):
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=5)
        try:
            await dev.async_write_schedule(protocol.power(True))
            await dev.async_write_schedule(protocol.power(False))
        finally:
            await dev.async_shutdown()

    run(scenario())
    assert sum(1 for w in client.writes if w[1] == protocol.CMD_CLOCK) == 1


def test_notifications_are_unsubscribed_before_the_link_drops(monkeypatch):
    # Some firmwares in this family wedge if a subscribed client just disappears.
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        await dev.async_write_schedule(protocol.power(True))
        await dev.async_shutdown()

    run(scenario())
    assert client.stop_notify_calls == 1
    assert not client.is_connected


def test_a_query_returns_whatever_record_comes_back(monkeypatch):
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            return await dev.async_query()
        finally:
            await dev.async_shutdown()

    assert run(scenario()).dispenses


# ---------------------------------------------------------------------------
# Reading several records at once — the #27 fall-through path
# ---------------------------------------------------------------------------


def _table_responder(table: dict[int, protocol.ScheduleRecord]):
    """A diffuser holding a specific set of records, answering 0xFC per record id."""

    def responder(frame: bytes) -> bytes | None:
        if frame[1] != protocol.CMD_QUERY:
            return None
        record = table[frame[2]]
        return protocol.echo_for(
            protocol.schedule(
                enabled=record.enabled, days=record.days, record=record.record,
                start=record.start, end=record.end, work=record.work, pause=record.pause,
            )
        )

    return responder


def _app_table() -> dict[int, protocol.ScheduleRecord]:
    """The phone app's three default schedules, as read off the device on 2026-08-09."""
    windows = {1: ((10, 0), (12, 0)), 2: ((12, 0), (18, 0)), 3: ((18, 0), (22, 0))}
    table = {
        rc: protocol.decode_echo(protocol.echo_for(protocol.schedule(
            enabled=True, days=protocol.ALL_DAYS, record=rc,
            start=start, end=end, work=6, pause=120,
        )))
        for rc, (start, end) in windows.items()
    }
    table[4] = protocol.decode_echo(protocol.echo_for(protocol.schedule(
        enabled=True, days=protocol.NO_DAYS, record=4,
        start=(0, 0), end=(0, 0), work=0, pause=0,
    )))
    return table


def test_a_batch_query_reads_every_record_over_one_link(monkeypatch):
    table = _app_table()
    client = FakeClient(_table_responder(table))
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            return await dev.async_query_records((2, 3, 4))
        finally:
            await dev.async_shutdown()

    answers = run(scenario())
    assert sorted(answers) == [2, 3, 4]
    # Each answer filed under the record that was actually asked for. Getting this wrong
    # would read as a device inconsistency rather than as a bug here.
    for record_id, record in answers.items():
        assert record.record == record_id
        assert (record.start, record.end) == (table[record_id].start, table[record_id].end)


def test_a_batch_query_ignores_an_answer_for_the_wrong_record(monkeypatch):
    """A reply arriving a beat late must not be filed under the next record queried.

    Without the record-id filter this is a silent mis-attribution rather than an error: the
    caller gets a perfectly valid, correctly-checksummed record under the wrong key, and the
    device gets the blame for being inconsistent.
    """
    table = _app_table()
    inner = _table_responder(table)
    client: FakeClient

    def responder(frame: bytes) -> bytes | None:
        if frame[1] == protocol.CMD_QUERY and frame[2] == 3:
            # The answer to the *previous* query, landing after we moved on — pushed first so
            # the real record-3 answer is queued behind it.
            client._notify_cb(None, bytearray(inner(protocol.status_query(2))))
        return inner(frame)

    client = FakeClient(responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            return await dev.async_query_records((3,))
        finally:
            await dev.async_shutdown()

    answers = run(scenario())
    assert answers[3].record == 3
    assert answers[3].start == (18, 0)  # Evenings, not Afternoons


# ---------------------------------------------------------------------------
# The rule: no echo, or the wrong echo, is a failure
# ---------------------------------------------------------------------------


def test_a_silent_device_fails_the_write_rather_than_reporting_success(monkeypatch):
    monkeypatch.setattr(device_mod, "ECHO_TIMEOUT_S", 0.2)
    client = FakeClient(lambda frame: None)  # accepts everything, confirms nothing
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    with pytest.raises(KirriCommandFailed, match="did not confirm"):
        run(scenario())
    # The write really was sent and accepted — which is precisely why accepting it as
    # success would have been wrong.
    assert any(w[1] == protocol.CMD_SCHEDULE for w in client.writes)


def test_an_echo_for_a_different_record_is_a_failure(monkeypatch):
    def wrong_echo(frame: bytes) -> bytes | None:
        if frame[1] == protocol.CMD_SCHEDULE:
            return protocol.echo_for(protocol.power(False))  # we asked for ON
        return None

    monkeypatch.setattr(device_mod, "ECHO_TIMEOUT_S", 0.5)
    _install(monkeypatch, FakeClient(wrong_echo))

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    with pytest.raises(KirriCommandFailed, match="echo mismatch"):
        run(scenario())


def test_an_unrelated_notification_does_not_break_a_command(monkeypatch):
    """A stray frame must be skipped, not mistaken for the answer."""

    def noisy(frame: bytes) -> bytes | None:
        if frame[1] != protocol.CMD_SCHEDULE:
            return None
        return protocol.echo_for(frame)

    client = FakeClient(noisy)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            # Inject junk of the length the never-observed live-status frame would have,
            # before the real echo arrives.
            original = client.write_gatt_char

            async def write_with_noise(uuid, data, response=True):
                if bytes(data)[1] == protocol.CMD_SCHEDULE and client._notify_cb:
                    client._notify_cb(None, bytearray(bytes(21)))
                await original(uuid, data, response)

            client.write_gatt_char = write_with_noise
            return await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    assert run(scenario()).dispenses


def test_a_stale_notification_cannot_satisfy_the_next_command(monkeypatch):
    monkeypatch.setattr(device_mod, "ECHO_TIMEOUT_S", 0.2)
    client = FakeClient(echo_responder)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=5)
        try:
            await dev.async_write_schedule(protocol.power(True))
            # Device spontaneously repeats the previous echo, then goes quiet.
            dev._handle_notification(None, bytearray(protocol.echo_for(protocol.power(True))))
            client._responder = lambda frame: None
            await dev.async_write_schedule(protocol.power(False))
        finally:
            await dev.async_shutdown()

    with pytest.raises(KirriCommandFailed):
        run(scenario())


# ---------------------------------------------------------------------------
# Silence is retried, disagreement is not — #35
# ---------------------------------------------------------------------------


def _run_command(monkeypatch, responder_for, command="write"):
    """Drive one command against a device whose links behave per ``responder_for``.

    Returns (result_or_raised, device, clients) so a test can inspect the counters, which are
    half of what #35 shipped.
    """
    monkeypatch.setattr(device_mod, "ECHO_TIMEOUT_S", 0.2)
    clients = _install_per_connect(monkeypatch, responder_for)
    box = {}

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        box["device"] = dev
        try:
            if command == "write":
                return await dev.async_write_schedule(protocol.power(True))
            return await dev.async_query()
        finally:
            await dev.async_shutdown()

    return scenario, box, clients


def test_a_write_whose_echo_never_arrives_succeeds_on_the_resend(monkeypatch):
    """#35, the whole of it: a silent first attempt used to fail the command outright.

    Three of the #14 soak's 22 verified writes died exactly here — the device took the write
    at the ATT layer, said nothing, and `KirriCommandFailed` walked straight out of
    `_async_run` because the retry caught only `BleakError` and `asyncio.TimeoutError`.
    """
    scenario, box, clients = _run_command(
        monkeypatch, lambda n: silent if n == 0 else echo_responder
    )
    record = run(scenario())

    assert record.dispenses
    assert len(clients) == 2, "the resend must go down a fresh link, not the one that failed"
    # And it really was resent, rather than the first write being credited retroactively.
    assert any(w[1] == protocol.CMD_SCHEDULE for w in clients[1].writes)


def test_a_read_whose_echo_never_arrives_is_retried_too(monkeypatch):
    """The defect is in the shared path, so the fix has to cover polls as well as commands.

    docs/lab/soak-2026-08.md reconciles a 13.6% write-failure rate with a 4.5% read-failure rate as
    one defect; a fix that only rescued writes would leave the larger population untouched —
    and a failed read is the worse symptom, since it takes the entity `unavailable` for a
    full poll interval rather than returning an error to somebody who is watching.
    """
    scenario, box, clients = _run_command(
        monkeypatch, lambda n: silent if n == 0 else echo_responder, command="query"
    )
    assert run(scenario()).dispenses
    assert len(clients) == 2


def test_a_device_that_stays_silent_still_fails_after_the_second_attempt(monkeypatch):
    """One retry, not a loop. The coordinator's next poll is what retries forever (#8)."""
    scenario, box, clients = _run_command(monkeypatch, lambda _n: silent)

    # The specific type reaches the caller rather than being re-wrapped in its own base
    # class, which would have repeated the address in the message and lost the detail.
    with pytest.raises(KirriEchoTimeout, match="did not confirm"):
        run(scenario())
    assert len(clients) == 2, "exactly two attempts, no more"
    health = box["device"].health
    assert (health.echo_retries, health.echo_recovered, health.echo_failures) == (1, 0, 1)


def test_an_echo_mismatch_is_not_retried(monkeypatch):
    """A device that answered with something else disagreed with us; it did not ignore us.

    Resending the same frame would likely reproduce the disagreement, and would hide a
    protocol bug worth seeing. This is why the missing-echo case needed its own exception
    rather than a wider `except` tuple.
    """

    def wrong_echo(frame: bytes) -> bytes | None:
        if frame[1] == protocol.CMD_SCHEDULE:
            return protocol.echo_for(protocol.power(False))  # we asked for ON
        return None

    scenario, box, clients = _run_command(monkeypatch, lambda _n: wrong_echo)

    with pytest.raises(KirriCommandFailed, match="echo mismatch") as raised:
        run(scenario())
    # Specifically *not* the retryable subclass. That distinction is the only thing keeping
    # this out of _RETRYABLE, so asserting the type is asserting the behaviour.
    assert not isinstance(raised.value, KirriEchoTimeout)
    assert len(clients) == 1, "one attempt only"
    health = box["device"].health
    assert (health.echo_timeouts, health.echo_retries) == (0, 0)


def test_the_retryable_timeout_is_still_a_command_failure():
    """Callers written before #35 catch KirriCommandFailed, and must keep working."""
    assert issubclass(KirriEchoTimeout, KirriCommandFailed)


# ---------------------------------------------------------------------------
# The counters — a rescued fault must not become an invisible one
# ---------------------------------------------------------------------------


def test_a_rescued_command_still_records_the_fault_that_caused_it(monkeypatch):
    """The instrument half of #35 (ADR-013), and the reason the fix is not just a wider except.

    Once the retry hides a missing echo, a link dropping 5% of its echoes and a link dropping
    none look identical from outside — the same trap ADR-012 caught for #38. The counter is
    what lets the verification soak tell a working retry from a box that simply had a quiet
    week.
    """
    scenario, box, clients = _run_command(
        monkeypatch, lambda n: silent if n == 0 else echo_responder
    )
    run(scenario())

    health = box["device"].health
    assert health.echo_timeouts == 1, "the fault happened, and is on record despite the rescue"
    assert (health.echo_retries, health.echo_recovered, health.echo_failures) == (1, 1, 0)
    assert health.last_retry_error and "KirriEchoTimeout" in health.last_retry_error
    assert health.last_retry is not None


def test_a_lost_write_and_a_lost_read_land_in_different_columns(monkeypatch):
    """Counted from the real call path, not from a flag a caller has to remember to pass.

    `expected is not None` *is* the split: only a write knows the frame it requires back,
    because it sent it. docs/lab/soak-2026-08.md argues reads and writes are one defect from two
    overlapping confidence intervals over a derived poll denominator — this is what lets a
    re-soak settle that directly.
    """
    scenario, box, _ = _run_command(monkeypatch, lambda _n: silent)
    with pytest.raises(KirriEchoTimeout):
        run(scenario())
    health = box["device"].health
    assert (health.write_echo_timeouts, health.read_echo_timeouts) == (2, 0)

    scenario, box, _ = _run_command(monkeypatch, lambda _n: silent, command="query")
    with pytest.raises(KirriEchoTimeout):
        run(scenario())
    health = box["device"].health
    assert (health.write_echo_timeouts, health.read_echo_timeouts) == (0, 2)


def test_every_attempt_that_times_out_is_counted_not_every_command(monkeypatch):
    """Two silent attempts are two faults, even though the caller sees one failure."""
    scenario, box, clients = _run_command(monkeypatch, lambda _n: silent)

    with pytest.raises(KirriEchoTimeout):
        run(scenario())
    assert box["device"].health.echo_timeouts == 2


def test_a_healthy_command_touches_no_counter(monkeypatch):
    clients = _install_per_connect(monkeypatch, lambda _n: echo_responder)
    box = {}

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        box["device"] = dev
        try:
            await dev.async_write_schedule(protocol.power(True))
        finally:
            await dev.async_shutdown()

    run(scenario())
    health = box["device"].health
    assert (health.echo_timeouts, health.echo_retries, health.echo_recovered,
            health.echo_failures, health.other_retries) == (0, 0, 0, 0, 0)
    assert (health.write_echo_timeouts, health.read_echo_timeouts) == (0, 0)
    assert len(clients) == 1


def test_a_diffuser_that_is_away_is_not_counted_as_a_command_failure(monkeypatch):
    """An absence is not a fault of ours — the same call PollHealth.not_attempted makes.

    Folding every mains cut into the measured fault rate would inflate it by exactly the case
    `device_present` already reports, correctly and for free.
    """
    _install(monkeypatch, None, advertising=False)
    box = {}

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        box["device"] = dev
        try:
            await dev.async_query()
        finally:
            await dev.async_shutdown()

    with pytest.raises(KirriNotFound):
        run(scenario())
    health = box["device"].health
    assert (health.echo_timeouts, health.echo_retries, health.echo_failures) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Address normalisation
# ---------------------------------------------------------------------------


def test_the_address_is_upper_cased_once_at_construction(monkeypatch):
    """Home Assistant keys its bluetooth history on the upper-case address.

    A lower-case one is the worst kind of bug here: connections still succeed, because
    bleak is not fussy, while async_address_present() reports the device permanently
    absent - so the entity sits unavailable next to a diffuser that is plainly working.
    """
    seen = {}

    def capture(hass, address, connectable=True):
        seen["address"] = address
        return object()

    monkeypatch.setattr(device_mod.bluetooth, "async_ble_device_from_address", capture)

    async def _establish(*_args, **_kwargs):
        return FakeClient(echo_responder)

    monkeypatch.setattr(device_mod, "establish_connection", _establish)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), "aa:bb:cc:dd:ee:ff", drain_seconds=0)
        assert dev.address == "AA:BB:CC:DD:EE:FF"
        try:
            await dev.async_query()
        finally:
            await dev.async_shutdown()

    run(scenario())
    assert seen["address"] == "AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------------------
# Absence and mistaken identity
# ---------------------------------------------------------------------------


def test_a_diffuser_that_is_not_advertising_raises_not_found(monkeypatch):
    _install(monkeypatch, None, advertising=False)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            await dev.async_query()
        finally:
            await dev.async_shutdown()

    # Distinct from a command failure: this is the normal post-power-cut state (#8), and the
    # coordinator reports it as unavailable rather than as an error.
    with pytest.raises(KirriNotFound, match="not advertising"):
        run(scenario())


def test_some_other_fff0_device_is_rejected(monkeypatch):
    client = FakeClient(echo_responder, has_service=False)
    _install(monkeypatch, client)

    async def scenario():
        dev = KirriBeaconDevice(FakeHass(), ADDRESS, drain_seconds=0)
        try:
            await dev.async_query()
        finally:
            await dev.async_shutdown()

    with pytest.raises(KirriNotSupported, match="not a Kirri Beacon"):
        run(scenario())
    assert not client.is_connected  # and we let go of it
