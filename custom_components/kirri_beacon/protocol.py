"""Kirri Beacon BLE frame codec.

Pure functions over bytes: **no I/O, no Bluetooth, no Home Assistant.** Everything here can
be exercised on a laptop with nothing plugged in, which is the point — the protocol is the
part that has to be exactly right, so it is the part that must be testable without hardware.

The spec this implements is ``docs/protocol.md``. It was recovered from the vendor's own
source maps and then confirmed against the physical device; the frames in ``KNOWN_GOOD`` and
``OBSERVED_ECHOES`` are the evidence, and ``tests/test_protocol.py`` checks this module
against them.

    A5 <cmd> <data...> <checksum>

    checksum = sum of ALL preceding bytes, including the A5 header, & 0xFF

⚠️ **No Home Assistant imports, and no intra-package imports.** The standard library is fine
(this module uses ``datetime``, ``dataclasses`` and ``typing``); what must not appear is
``import homeassistant...`` or a relative ``from .const import ...``. The module has to stay
importable *directly*, not only as part of the ``kirri_beacon`` package, because
``tools/frames.py`` and the test suite both load it standalone - a relative import would
silently break both. Anything needing Home Assistant belongs in another file.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Final, Iterable, Sequence

HEADER: Final = 0xA5

CMD_SCHEDULE: Final = 0xFA  # -> device: write schedule record (also power and intensity)
CMD_ECHO: Final = 0xFB  # <- device: schedule echo
CMD_QUERY: Final = 0xFC  # -> device: query status
CMD_CLOCK: Final = 0xFD  # -> device: clock sync

SCHEDULE_FRAME_LEN: Final = 14
LIVE_STATUS_MIN_LEN: Final = 21

# Record IDs the device exposes. 0 is deliberately excluded - see write_record_guard().
MIN_RECORD: Final = 1
MAX_RECORD: Final = 4

# Both fields are real big-endian uint16s, so this is the protocol limit, not a UI one.
MAX_SECONDS: Final = 0xFFFF

# Ordered Monday-first, and that order is load-bearing: day_bit() indexes this rather than
# carrying a second weekday table that could drift out of step with it.
DAY_BITS: Final = {
    "mon": 0x01,
    "tue": 0x02,
    "wed": 0x04,
    "thu": 0x08,
    "fri": 0x10,
    "sat": 0x20,
    "sun": 0x40,
}
NO_DAYS: Final = 0x00
ALL_DAYS: Final = 0x7F

# Intensity *is* the work/pause ratio on this model - there is no intensity register.
# Names are user-facing (they become the HA select options), so they match Kirri's own.
INTENSITY_PRESETS: Final[dict[str, tuple[int, int]]] = {
    "Delicate": (6, 120),
    "Subtle": (12, 120),
    "Radiant": (18, 120),
    "Intense": (24, 120),
}
DEFAULT_INTENSITY: Final = "Delicate"
DEFAULT_PAUSE_S: Final = 120

# The whole-day window every frame power() builds carries. Not a schedule - see power().
#
# This window is the integration's grip on the device. A record covering all seven days from
# 00:00 to 23:59 matches at every instant, and the device runs the *first matching record by
# id* (active_record) - so a record 1 shaped like this permanently outranks whatever the phone
# app has left in records 2-4, in both directions. Narrow it and power-off stops working; see
# #27 and ADR-011.
ALL_DAY_START: Final = (0, 0)
ALL_DAY_END: Final = (23, 59)

#: The dispense time that means "on the air, dispensing nothing" - how *off* is expressed.
#:
#: The whole of #27's fix rests on this value being both accepted and inert, and it is: on
#: 2026-08-09 an enabled all-day record 1 carrying ``work = 0`` was written while record 2 was
#: actively dispensing at 6 s / 10 s. The device echoed it verbatim, read it back verbatim,
#: and the mist stopped. See power().
OFF_WORK_S: Final = 0


class ProtocolError(ValueError):
    """A frame could not be built or parsed.

    Subclasses ValueError so callers that only care that it was bad input can catch either.
    """


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def checksum(data: Sequence[int]) -> int:
    """Sum of every preceding byte, including the A5 header, mod 256.

    Note this differs from the Element/Aroma-Link family, which XORs and covers only the
    payload. Using the wrong one produces frames the device silently ignores.
    """
    return sum(data) & 0xFF


def frame(cmd: int, data: Sequence[int] = ()) -> bytes:
    """Wrap a command and payload into a checksummed frame."""
    pkt = [HEADER, cmd, *data]
    pkt.append(checksum(pkt))
    return bytes(pkt)


def validate(pkt: bytes) -> bool:
    """True if ``pkt`` has the A5 header and a correct trailing checksum."""
    return len(pkt) > 2 and pkt[0] == HEADER and pkt[-1] == checksum(pkt[:-1])


# ---------------------------------------------------------------------------
# Outbound commands
# ---------------------------------------------------------------------------


def write_record_guard(record: int) -> None:
    """Raise unless ``record`` is a user record (1-4).

    Record 0 is refused **in code, not by convention**. The vendor's own source carries the
    warning that it may act as a master power switch and that clearing it could disable the
    device permanently. The app never touches it and neither do we; there is no reason to
    repeat that experiment on hardware we own.
    """
    if not isinstance(record, int) or isinstance(record, bool):
        raise ProtocolError(f"record must be an int, got {record!r}")
    if record == 0:
        raise ProtocolError(
            "refusing to write record 0: the vendor's source warns it may be a master "
            "power switch and that writing it could disable the device"
        )
    if not MIN_RECORD <= record <= MAX_RECORD:
        raise ProtocolError(f"record must be {MIN_RECORD}-{MAX_RECORD}, got {record}")


def _check_time(label: str, hm: tuple[int, int]) -> None:
    hour, minute = hm
    if not 0 <= hour <= 23:
        raise ProtocolError(f"{label} hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ProtocolError(f"{label} minute must be 0-59, got {minute}")


def _check_seconds(label: str, value: int) -> None:
    if not 0 <= value <= MAX_SECONDS:
        raise ProtocolError(f"{label} must be 0-{MAX_SECONDS} seconds, got {value}")


def schedule(
    *,
    enabled: bool,
    days: int,
    record: int,
    start: tuple[int, int],
    end: tuple[int, int],
    work: int,
    pause: int,
) -> bytes:
    """Build a 0xFA schedule record - the only command that changes anything.

    ``start``/``end`` are (hour, minute). ``work``/``pause`` are seconds, big-endian uint16.
    """
    write_record_guard(record)
    if not NO_DAYS <= days <= ALL_DAYS:
        raise ProtocolError(f"day mask must be 0x00-0x7F, got 0x{days:02X}")
    _check_time("start", start)
    _check_time("end", end)
    _check_seconds("work", work)
    _check_seconds("pause", pause)

    return frame(
        CMD_SCHEDULE,
        [
            0x01 if enabled else 0x00,
            days,
            record,
            start[0],
            start[1],
            end[0],
            end[1],
            (work >> 8) & 0xFF,
            work & 0xFF,
            (pause >> 8) & 0xFF,
            pause & 0xFF,
        ],
    )


def power(on: bool, work: int = 6, pause: int = DEFAULT_PAUSE_S, record: int = 1) -> bytes:
    """Turn the diffuser on or off.

    **There is no power command.** Both directions are the *same* record - every day, all
    day - and they differ in exactly one field: ``work``. On carries the selected dispense
    time; off carries ``work = 0``.

    ⚠️ **Off does not clear the record, and must not.** The vendor's own ``createPowerCommand``
    expresses off by zeroing the day mask, and this function did the same until #27. That is
    wrong on any device that has ever had the phone app's schedule screen saved: a record
    matching no days matches *nothing*, so the device **falls through** to records 2-4 and goes
    on dispensing from the app's schedules. The write is echoed back perfectly, the readback of
    record 1 truthfully reports zeros, and Home Assistant reports off while the diffuser mists.
    That failure is the vendor's design, not our misreading of it - their app's own power-off
    has the same hole.

    Holding the record instead of clearing it is what fixes it. An enabled, all-day record 1
    matches at every instant, so arbitration stops there and never reaches records 2-4
    (active_record); ``work = 0`` then means it matches without dispensing. Verified on
    hardware 2026-08-09 with record 2 actively bursting at the time - see OFF_WORK_S,
    docs/protocol.md → *Record arbitration*, and ADR-011.

    Two consequences worth stating rather than discovering:

    * **while this integration owns record 1, the phone app's schedules never fire.** They are
      masked, not erased - clearing record 1 by hand hands control straight back (ADR-009).
    * **off still loses the intensity**, because ``work`` is where the intensity lives. The
      caller has to remember it to turn back on; IntensityMemory is where that happens, and it
      already refuses to absorb a ``work = 0`` echo for exactly this reason.

    The enabled byte stays ``0x01`` in both directions. The app hardcodes it that way, every
    frame this device has ever been observed to accept has it set, and no record with it
    cleared has ever been seen - so it is copied, not interpreted.
    """
    return schedule(
        enabled=True,
        days=ALL_DAYS,
        record=record,
        start=ALL_DAY_START,
        end=ALL_DAY_END,
        work=work if on else OFF_WORK_S,
        pause=pause,
    )


def intensity(name: str, record: int = 1) -> bytes:
    """Power-on frame for a named preset. Raises on an unknown name."""
    if name not in INTENSITY_PRESETS:
        raise ProtocolError(
            f"unknown intensity {name!r}; expected one of {', '.join(INTENSITY_PRESETS)}"
        )
    work, pause = INTENSITY_PRESETS[name]
    return power(True, work=work, pause=pause, record=record)


def status_query(record: int = 1) -> bytes:
    """Ask the device to echo a record back.

    Records 1-4 all answer, and the reply carries back the record id that was asked for
    (verified on hardware 2026-08-09). An empty record answers too, with a correctly
    labelled zeroed frame - so "empty" is readable rather than inferred from silence.
    See ``docs/protocol.md``, "0xFC - status query".
    """
    write_record_guard(record)
    return frame(CMD_QUERY, [record, 0x00, 0x00, 0x00])


def clock_sync(weekday: int, hour: int, minute: int, second: int) -> bytes:
    """Build a 0xFD clock-sync frame.

    ``weekday`` is **1-7 with Monday = 1 and Sunday = 7** - ISO numbering, not Python's
    0-6 ``weekday()``. Use clock_sync_now() rather than converting by hand.

    The device evaluates schedules against its own RTC - but on-device schedules were dropped
    (#17, ADR-009) and the power path uses an all-day/all-days window, so nothing we send is
    read against that clock. Sent as a courtesy, and to keep the option open.

    ⚠️ **Fire-and-forget: this frame is never acknowledged.** Verified on the device
    2026-08-09 — no notification comes back at all, though the device stays responsive and
    the schedule record is untouched. It is the only command here with no readback, so the
    "a write succeeded when its echo matched" rule cannot apply. Anything that awaits an echo
    after sending this will stall for its full timeout on every connect.
    """
    if not 1 <= weekday <= 7:
        raise ProtocolError(f"weekday must be 1-7 (Mon=1, Sun=7), got {weekday}")
    if not 0 <= hour <= 23:
        raise ProtocolError(f"hour must be 0-23, got {hour}")
    if not 0 <= minute <= 59:
        raise ProtocolError(f"minute must be 0-59, got {minute}")
    if not 0 <= second <= 59:
        raise ProtocolError(f"second must be 0-59, got {second}")
    return frame(CMD_CLOCK, [weekday, hour, minute, second, *([0x00] * 6)])


def clock_sync_now(now: _dt.datetime) -> bytes:
    """clock_sync() for a datetime, doing the Monday=1 conversion correctly.

    Pass local time: the device has no concept of a timezone, it just holds wall-clock.
    """
    return clock_sync(now.isoweekday(), now.hour, now.minute, now.second)


def echo_for(sent: bytes) -> bytes:
    """The echo the device is expected to return for a 0xFA write.

    Same payload, command byte 0xFB, recomputed checksum. This is how a write is confirmed:
    an ATT-layer accept proves nothing on this device - a 0-byte write is accepted too
    (``docs/research/dossier.md`` 6.5/7.4) - so success means *this frame came back*.
    """
    if len(sent) != SCHEDULE_FRAME_LEN or sent[0] != HEADER or sent[1] != CMD_SCHEDULE:
        raise ProtocolError(f"not a 0xFA schedule write: {sent.hex().upper()}")
    return frame(CMD_ECHO, sent[2:-1])


# ---------------------------------------------------------------------------
# Inbound frames
# ---------------------------------------------------------------------------


#: DAY_BITS' values in its own Monday-first order, so day_bit() cannot disagree with it.
_ISO_DAY_BITS: Final[tuple[int, ...]] = tuple(DAY_BITS.values())


def day_bit(iso_weekday: int) -> int:
    """The day-mask bit for an ISO weekday - **Monday = 1 through Sunday = 7**.

    ISO numbering, not Python's 0-6 ``datetime.weekday()`` - the same convention clock_sync()
    uses, and the same one ``datetime.isoweekday()`` returns. Converting by hand is how the
    mask ends up rotated by a day, which is invisible six days out of seven.
    """
    if not 1 <= iso_weekday <= 7:
        raise ProtocolError(f"weekday must be 1-7 (Mon=1, Sun=7), got {iso_weekday}")
    return _ISO_DAY_BITS[iso_weekday - 1]


@dataclass(frozen=True)
class ScheduleRecord:
    """A decoded 0xFB echo - what the device says its record currently holds."""

    enabled: bool
    days: int
    record: int
    start: tuple[int, int]
    end: tuple[int, int]
    work: int
    pause: int

    def matches_at(self, now: _dt.datetime) -> bool:
        """Whether this record's day mask **and** time window cover ``now``.

        This is the *arbitration* test, and it is deliberately not "is it dispensing": a
        record that matches with ``work = 0`` still matches, and still stops the device
        falling through to the next record. That distinction is the entire mechanism behind
        power-off - see power() and active_record().

        The ``enabled`` byte is **not** consulted, and that is a considered choice rather than
        an oversight. The device has never once been observed to hold a record with it
        cleared: the app hardcodes ``0x01``, and expresses "off" through the day mask instead
        (docs/protocol.md → *How power works*). Both readings are therefore untested, and they
        fail in opposite directions - honouring a flag the device ignores would report *off*
        while the diffuser mists, which is precisely the #27 failure this module exists to stop
        repeating. Ignoring a flag the device honours only reports *on* while it is silent,
        which the user can see out of the corner of their eye. The cheaper mistake wins until
        somebody measures it.
        """
        if not self.days & day_bit(now.isoweekday()):
            return False
        minute = now.hour * 60 + now.minute
        start = self.start[0] * 60 + self.start[1]
        end = self.end[0] * 60 + self.end[1]
        if start <= end:
            # Inclusive at both ends. Our own window is 00:00-23:59, which has to match
            # during the final minute of the day or off would lapse for 60 seconds nightly.
            # What the device does at an app record's exact boundary minute is unmeasured -
            # at 12:00 both a 10:00-12:00 and a 12:00-18:00 record read as matching here, and
            # the lower id wins, which is the same answer either way.
            return start <= minute <= end
        # A window running backwards over midnight. The app never produces one and the device
        # has never been asked to evaluate one, so this is an assumption: treat it as wrapping
        # rather than as never matching, since "never matches" would silently disable a record
        # the user believes is armed.
        return minute >= start or minute <= end

    def is_dispensing_at(self, now: _dt.datetime) -> bool:
        """Whether this record would be producing mist at ``now``."""
        return self.work > 0 and self.matches_at(now)

    @property
    def dispenses(self) -> bool:
        """Whether this record can *ever* dispense - a real day mask and a non-zero burst.

        Clock-free, so it describes the record rather than the moment; it is what the CLI
        tools print. For "is the diffuser misting right now" you need a time and, because of
        fall-through, every record - DeviceState.
        """
        return self.days != NO_DAYS and self.work > 0

    @property
    def intensity_name(self) -> str | None:
        """The preset matching work/pause, or None if the values are custom."""
        for name, work_pause in INTENSITY_PRESETS.items():
            if work_pause == (self.work, self.pause):
                return name
        return None


def active_record(
    records: Iterable[ScheduleRecord], now: _dt.datetime
) -> ScheduleRecord | None:
    """The record the device is running at ``now``, or None if none of them match.

    **The arbitration rule, established on hardware 2026-08-09 (#27):** the device runs the
    *first record by id* whose day mask and time window match. A record that does not match is
    skipped and the device falls through to the next one. It does **not** dispense on the union
    of matching records, and it does not prefer the most recently written one - record 1 won a
    controlled test even when written *before* record 2.

    Note what a match does *not* require: mist. A matching record carrying ``work = 0`` wins
    arbitration and then dispenses nothing, which stops the search dead rather than letting it
    reach records 2-4. That is why power-off is a record we hold rather than a record we clear.

    Pass whichever records you actually read - the caller decides how much to spend on radio,
    and reading only record 1 is the right answer whenever record 1 matches, because nothing
    behind it can be reached.
    """
    for record in sorted(records, key=lambda rec: rec.record):
        if record.matches_at(now):
            return record
    return None


@dataclass(frozen=True)
class DeviceState:
    """What the diffuser is doing, resolved across the records that were read.

    ⚠️ **A single record cannot answer "is it on".** Believing it could *was* #27: Home
    Assistant modelled record 1 as if it were the whole device, so turning off zeroed record 1,
    the device fell through to the phone app's record 2, and HA reported off while the atomiser
    ran. Anything asking about power state asks this object, not a ScheduleRecord.

    ``records`` holds only what was actually queried, keyed by record id - which is usually
    just record 1, because a matching record 1 makes the rest unreachable. ``at`` is the moment
    the records were read; the answer is resolved against that rather than against "now" so a
    state and the reading it came from can never disagree.
    """

    records: dict[int, ScheduleRecord]
    at: _dt.datetime

    @property
    def active(self) -> ScheduleRecord | None:
        """The record that won arbitration, or None if nothing matched."""
        return active_record(self.records.values(), self.at)

    @property
    def is_on(self) -> bool:
        """Whether the diffuser is dispensing."""
        active = self.active
        return active is not None and active.work > 0

    @property
    def is_owned(self) -> bool:
        """Whether record 1 is the always-matching record this integration writes.

        False means the phone app has taken record 1 back, so the device is arbitrating over
        records this integration did not write and the next on/off will reclaim it.
        """
        record = self.records.get(1)
        return (
            record is not None
            and record.days == ALL_DAYS
            and record.start == ALL_DAY_START
            and record.end == ALL_DAY_END
        )


@dataclass(frozen=True)
class LiveStatus:
    """A decoded 21+ byte live-status notification.

    ⚠️ **SPECULATIVE - this frame has never been observed on air.** The layout is read out
    of the vendor's parser, not captured from the device. Every notification seen to date
    (#5's four, all of #8's) has been the 14-byte 0xFB echo. It may only appear while the
    device is actively dispensing, or it may be dead code inherited from the Element family.

    Kept so that if one ever does arrive we decode it rather than discard it. Nothing should
    depend on it existing - see #13's conditional acceptance criteria.
    """

    intensity: int
    is_on: bool
    work_status: int
    work_remaining: int
    pause_remaining: int
    raw: bytes


def decode_echo(pkt: bytes) -> ScheduleRecord:
    """Decode a 14-byte 0xFB schedule echo. Raises ProtocolError on anything else."""
    if len(pkt) != SCHEDULE_FRAME_LEN:
        raise ProtocolError(f"expected {SCHEDULE_FRAME_LEN} bytes, got {len(pkt)}")
    if pkt[0] != HEADER or pkt[1] != CMD_ECHO:
        raise ProtocolError(f"not a 0xFB echo (starts {pkt[0]:02X} {pkt[1]:02X})")
    if not validate(pkt):
        raise ProtocolError(
            f"bad checksum (want {checksum(pkt[:-1]):02X}, got {pkt[-1]:02X})"
        )
    return ScheduleRecord(
        enabled=bool(pkt[2]),
        days=pkt[3],
        record=pkt[4],
        start=(pkt[5], pkt[6]),
        end=(pkt[7], pkt[8]),
        work=(pkt[9] << 8) | pkt[10],
        pause=(pkt[11] << 8) | pkt[12],
    )


def decode_live_status(pkt: bytes) -> LiveStatus:
    """Decode a 21+ byte live-status notification. See LiveStatus - never yet observed.

    The checksum is deliberately **not** enforced here. We know the trailing byte of the
    14-byte frames is a checksum; we do not know that the same holds for this one, because
    nobody has ever seen it. Rejecting a real frame over a guessed rule would be the worst
    possible outcome for a frame we are trying to catch in the wild.
    """
    if len(pkt) < LIVE_STATUS_MIN_LEN:
        raise ProtocolError(f"expected >={LIVE_STATUS_MIN_LEN} bytes, got {len(pkt)}")
    return LiveStatus(
        intensity=pkt[14] & 0x0F,
        is_on=pkt[15] == 0x01,
        work_status=pkt[16],
        work_remaining=(pkt[17] << 8) | pkt[18],
        pause_remaining=(pkt[19] << 8) | pkt[20],
        raw=bytes(pkt),
    )


def is_echo(pkt: bytes) -> bool:
    """Cheap classifier: is this the 14-byte schedule echo?"""
    return len(pkt) == SCHEDULE_FRAME_LEN and pkt[0] == HEADER and pkt[1] == CMD_ECHO


def is_live_status(pkt: bytes) -> bool:
    """Cheap classifier for the speculative 21+ byte frame.

    Length-based on purpose: bytes 0-13 are unparsed by the app, so we cannot assume this
    frame even carries the A5 header.
    """
    return len(pkt) >= LIVE_STATUS_MIN_LEN


# ---------------------------------------------------------------------------
# Human-readable helpers (used by tools/ and by debug logging)
# ---------------------------------------------------------------------------


def describe_days(mask: int) -> str:
    if mask == NO_DAYS:
        return "none"
    if mask == ALL_DAYS:
        return "all"
    return ",".join(name for name, bit in DAY_BITS.items() if mask & bit)


def describe_echo(pkt: bytes) -> str:
    """One-line summary of a 0xFB echo, for logs and the CLI tools."""
    rec = decode_echo(pkt)
    name = rec.intensity_name
    return (
        f"record {rec.record} · {'ON' if rec.dispenses else 'OFF'} · "
        f"days={describe_days(rec.days)}(0x{rec.days:02X}) · "
        f"{rec.start[0]:02d}:{rec.start[1]:02d}-{rec.end[0]:02d}:{rec.end[1]:02d} · "
        f"work={rec.work}s pause={rec.pause}s" + (f" ({name})" if name else "")
    )


def hexs(pkt: bytes) -> str:
    return " ".join(f"{b:02X}" for b in pkt)


# ---------------------------------------------------------------------------
# Evidence
#
# These are not examples - they are the captured record that this module is tested against.
# ---------------------------------------------------------------------------

#: Outbound frames, with the hex confirmed against docs/protocol.md.
KNOWN_GOOD: Final[dict[str, str]] = {
    "power on (Delicate)": "A5FA017F010000173B00060078F0",
    "power off": "A5FA017F010000173B00000078EA",
    "status query": "A5FC01000000A2",
    "clock sync Fri 20:15:00": "A5FD05140F00000000000000CA",
}

#: Frames the device actually sent back, captured through the proxy.
OBSERVED_ECHOES: Final[dict[str, str]] = {
    # 2026-08-08, #13.
    "echo of power on (Delicate)": "A5FB017F010000173B00060078F1",
    # 2026-08-09 20:02, #27 phase 4b - and the strongest single piece of evidence in this
    # file. The frame was sent *while record 2 was actively dispensing* at 6 s / 10 s. The
    # device echoed it verbatim, a 0xFC readback two minutes later returned it verbatim, and
    # the mist stopped and stayed stopped. Off holding the record instead of clearing it is
    # not a design we reasoned our way to - it is a measurement.
    "echo of power off (work = 0)": "A5FB017F010000173B00000078EB",
}

#: The OFF frame this integration sent **before** #27, with the echo the device returned for
#: it on 2026-08-08.
#:
#: Kept as evidence, not as a spare. It clears record 1 rather than holding it, so on any unit
#: carrying the phone app's schedules it is echoed back perfectly and leaves the diffuser
#: running - see power(). Nothing builds it any more; it is here so that a capture containing
#: it can still be recognised for what it is, including one taken from the vendor's own app.
RETIRED_OFF: Final[dict[str, str]] = {
    "sent": "A5FA0100010000000000000000A1",
    "echo": "A5FB0100010000000000000000A2",
}
