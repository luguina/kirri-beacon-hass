"""Tests for the Kirri Beacon frame codec.

No Home Assistant, no Bluetooth, no hardware — ``pytest`` and nothing else. The point of
keeping ``protocol.py`` pure is that the part which has to be byte-exact can be proved
without the diffuser in the room.

The vectors here are evidence, not fixtures: ``KNOWN_GOOD`` is what ``docs/protocol.md``
documents, and ``OBSERVED_ECHOES`` is what the device actually sent back on 2026-08-08.
"""

from __future__ import annotations

import datetime as dt

import pytest

import protocol as p

#: 2026-08-10 was a Monday, so this is one full week starting on an ISO weekday 1 — every
#: day-mask bit and every hour, which is what "always matches" has to survive.
_EVERY_HOUR_OF_THE_WEEK = tuple(
    dt.datetime(2026, 8, 10) + dt.timedelta(hours=h) for h in range(7 * 24)
)


def _at(day: str, hhmm: str) -> dt.datetime:
    """A datetime on a named weekday of the reference week, e.g. _at("wed", "13:37")."""
    index = list(p.DAY_BITS).index(day)
    hour, minute = (int(part) for part in hhmm.split(":"))
    return dt.datetime(2026, 8, 10 + index, hour, minute)


def _record(record=1, *, days=p.ALL_DAYS, start=(0, 0), end=(23, 59), work=6, pause=120):
    return p.ScheduleRecord(
        enabled=True, days=days, record=record, start=start, end=end, work=work, pause=pause
    )


#: The phone app's three default schedules, exactly as read off the device on 2026-08-09.
#: Fixtures elsewhere in this file are invented; these are not — see docs/protocol.md.
_APP_SCHEDULES = (
    _record(1, start=(10, 0), end=(12, 0)),
    _record(2, start=(12, 0), end=(18, 0)),
    _record(3, start=(18, 0), end=(22, 0)),
    _record(4, days=p.NO_DAYS, start=(0, 0), end=(0, 0), work=0, pause=0),
)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------


def test_checksum_is_sum_mod_256_including_the_header():
    # The Element/Aroma-Link family XORs the payload only; getting this wrong produces
    # frames the device silently ignores, which is why it is asserted explicitly.
    assert p.checksum([0xA5, 0xFC, 0x01, 0x00, 0x00, 0x00]) == 0xA2
    assert p.checksum([]) == 0x00
    assert p.checksum([0xFF, 0xFF]) == 0xFE  # wraps


def test_frame_appends_checksum_over_the_whole_frame():
    pkt = p.frame(0xFC, [0x01, 0x00, 0x00, 0x00])
    assert pkt[0] == p.HEADER
    assert pkt[1] == 0xFC
    assert pkt[-1] == p.checksum(pkt[:-1])


@pytest.mark.parametrize(
    "pkt",
    [
        b"",
        b"\xa5",
        b"\xa5\xfb",
        b"\xa5\xfc\x01\x00\x00\x00\xa3",  # checksum off by one
        b"\xb5\xfc\x01\x00\x00\x00\xa2",  # wrong header
    ],
)
def test_validate_rejects_bad_frames(pkt):
    assert not p.validate(pkt)


def test_validate_accepts_every_known_good_frame():
    for name, hexstr in p.KNOWN_GOOD.items():
        assert p.validate(bytes.fromhex(hexstr)), name


# ---------------------------------------------------------------------------
# The documented vectors
# ---------------------------------------------------------------------------


def test_power_on_matches_the_device_verified_frame():
    # This exact frame turned the diffuser on from nRF Connect (docs/protocol.md).
    assert p.power(True).hex().upper() == p.KNOWN_GOOD["power on (Delicate)"]


def test_power_off_matches_the_device_verified_frame():
    # Verified twice over on 2026-08-09 (#27 phase 4b): this exact frame was written while
    # record 2 was actively dispensing, echoed back verbatim, read back verbatim two minutes
    # later, and the mist stopped.
    assert p.power(False).hex().upper() == p.KNOWN_GOOD["power off"]


def test_status_query_matches_the_documented_frame():
    assert p.status_query().hex().upper() == p.KNOWN_GOOD["status query"]


def test_clock_sync_matches_the_documented_frame():
    assert p.clock_sync(5, 20, 15, 0).hex().upper() == p.KNOWN_GOOD["clock sync Fri 20:15:00"]


def test_every_frame_is_the_documented_length():
    assert len(p.power(True)) == p.SCHEDULE_FRAME_LEN
    assert len(p.power(False)) == p.SCHEDULE_FRAME_LEN
    assert len(p.status_query()) == 7
    assert len(p.clock_sync(1, 0, 0, 0)) == 13


# ---------------------------------------------------------------------------
# Record 0 — the safety rule, asserted in code rather than trusted to convention
# ---------------------------------------------------------------------------


def test_record_zero_is_refused_everywhere_it_could_be_written():
    with pytest.raises(p.ProtocolError, match="master power switch"):
        p.write_record_guard(0)
    with pytest.raises(p.ProtocolError, match="master power switch"):
        p.schedule(
            enabled=True, days=p.ALL_DAYS, record=0,
            start=(0, 0), end=(23, 59), work=6, pause=120,
        )
    with pytest.raises(p.ProtocolError, match="master power switch"):
        p.power(True, record=0)
    with pytest.raises(p.ProtocolError, match="master power switch"):
        p.status_query(record=0)


@pytest.mark.parametrize("record", [-1, 5, 255])
def test_records_outside_one_to_four_are_refused(record):
    with pytest.raises(p.ProtocolError):
        p.write_record_guard(record)


def test_bool_is_not_accepted_as_a_record():
    # True == 1 in Python, so a stray boolean would otherwise sail through as record 1.
    with pytest.raises(p.ProtocolError, match="must be an int"):
        p.write_record_guard(True)


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"days": 0x80},
        {"days": -1},
        {"start": (24, 0)},
        {"start": (0, 60)},
        {"end": (-1, 0)},
        {"work": -1},
        {"work": 0x10000},
        {"pause": 0x10000},
    ],
)
def test_out_of_range_fields_are_refused(kwargs):
    base = dict(
        enabled=True, days=p.ALL_DAYS, record=1,
        start=(0, 0), end=(23, 59), work=6, pause=120,
    )
    with pytest.raises(p.ProtocolError):
        p.schedule(**{**base, **kwargs})


def test_work_and_pause_are_big_endian_uint16():
    pkt = p.schedule(
        enabled=True, days=p.ALL_DAYS, record=1,
        start=(0, 0), end=(23, 59), work=0x0102, pause=0x0304,
    )
    assert pkt[9:11] == b"\x01\x02"
    assert pkt[11:13] == b"\x03\x04"


def test_seconds_above_a_byte_survive_a_round_trip():
    # Guards the byte-order handling for values the presets never reach.
    pkt = p.schedule(
        enabled=True, days=p.ALL_DAYS, record=2,
        start=(1, 2), end=(3, 4), work=1000, pause=65535,
    )
    rec = p.decode_echo(p.echo_for(pkt))
    assert (rec.work, rec.pause) == (1000, 65535)


# ---------------------------------------------------------------------------
# Power semantics
# ---------------------------------------------------------------------------


def test_power_off_keeps_the_enabled_byte_set():
    # The app hardcodes PW=0x01 even for off. This looks wrong and is correct — every frame
    # this device has ever been observed to accept has it set.
    assert p.power(False)[2] == 0x01


def test_power_off_holds_record_one_rather_than_clearing_it():
    """#27's regression test, and the single most important assertion in this file.

    Off used to zero the day mask. A record matching no days matches nothing, so the device
    fell through to whatever the phone app had left in records 2–4 and carried on misting
    while Home Assistant reported off. Off must keep matching — that is what stops the search
    at record 1 — and express "off" through the dispense time instead.
    """
    off = p.power(False)
    assert off.hex().upper() != p.RETIRED_OFF["sent"]

    rec = p.decode_echo(p.echo_for(off))
    assert rec.days == p.ALL_DAYS
    assert (rec.start, rec.end) == (p.ALL_DAY_START, p.ALL_DAY_END)
    assert rec.work == p.OFF_WORK_S == 0
    # Matches at every instant, and dispenses at none of them.
    for now in _EVERY_HOUR_OF_THE_WEEK:
        assert rec.matches_at(now), now
        assert not rec.is_dispensing_at(now), now


def test_power_off_still_costs_the_device_its_memory_of_the_intensity():
    # The reason intensity has to be remembered in Home Assistant rather than read back:
    # work *is* the intensity, so an off frame cannot carry one.
    rec = p.decode_echo(p.echo_for(p.power(False, work=24, pause=120)))
    assert rec.work == 0
    assert rec.intensity_name is None
    assert not rec.dispenses


def test_power_on_and_off_differ_only_in_the_dispense_time():
    on = p.power(True, work=18, pause=90)
    off = p.power(False, work=18, pause=90)
    differing = [i for i, (a, b) in enumerate(zip(on, off)) if a != b]
    # Byte 10 is the low half of the big-endian work field; byte 13 is the checksum.
    assert differing == [10, 13]


def test_power_on_covers_every_day_and_the_whole_day():
    rec = p.decode_echo(p.echo_for(p.power(True)))
    assert rec.days == p.ALL_DAYS
    assert rec.start == (0, 0)
    assert rec.end == (23, 59)
    assert rec.dispenses
    for now in _EVERY_HOUR_OF_THE_WEEK:
        assert rec.is_dispensing_at(now), now


def test_power_on_carries_the_supplied_intensity():
    rec = p.decode_echo(p.echo_for(p.power(True, work=24, pause=120)))
    assert (rec.work, rec.pause) == (24, 120)
    assert rec.intensity_name == "Intense"


# ---------------------------------------------------------------------------
# Intensity presets
# ---------------------------------------------------------------------------


def test_each_preset_builds_a_distinct_frame():
    frames = {name: p.intensity(name) for name in p.INTENSITY_PRESETS}
    assert len(set(frames.values())) == len(p.INTENSITY_PRESETS)


@pytest.mark.parametrize("name,work", [("Delicate", 6), ("Subtle", 12), ("Radiant", 18), ("Intense", 24)])
def test_presets_carry_the_documented_dispense_times(name, work):
    rec = p.decode_echo(p.echo_for(p.intensity(name)))
    assert rec.work == work
    assert rec.pause == p.DEFAULT_PAUSE_S
    assert rec.intensity_name == name


def test_unknown_intensity_is_refused():
    with pytest.raises(p.ProtocolError, match="unknown intensity"):
        p.intensity("Overwhelming")


def test_default_intensity_is_a_real_preset():
    assert p.DEFAULT_INTENSITY in p.INTENSITY_PRESETS


# ---------------------------------------------------------------------------
# Clock sync
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_clock_sync_refuses_weekdays_outside_one_to_seven(weekday):
    with pytest.raises(p.ProtocolError, match="weekday"):
        p.clock_sync(weekday, 0, 0, 0)


@pytest.mark.parametrize("h,m,s", [(24, 0, 0), (0, 60, 0), (0, 0, 60), (-1, 0, 0)])
def test_clock_sync_refuses_out_of_range_times(h, m, s):
    with pytest.raises(p.ProtocolError):
        p.clock_sync(1, h, m, s)


def test_clock_sync_now_uses_monday_equals_one_not_pythons_zero():
    # dt.weekday() is Mon=0; the device wants Mon=1. Getting this wrong hands the device a
    # day-shifted clock, and the frame is never acknowledged - so this test is the only
    # defence. More so since on-device schedules were dropped (#17, ADR-009): nothing
    # downstream reads that clock any more, so no behaviour would ever surface the mistake.
    monday = dt.datetime(2026, 8, 3, 9, 30, 15)
    assert monday.weekday() == 0
    assert p.clock_sync_now(monday)[2] == 1

    sunday = dt.datetime(2026, 8, 9, 9, 30, 15)
    assert p.clock_sync_now(sunday)[2] == 7


def test_clock_sync_now_carries_the_wall_clock():
    when = dt.datetime(2026, 8, 7, 20, 15, 0)
    assert p.clock_sync_now(when) == p.clock_sync(5, 20, 15, 0)


# ---------------------------------------------------------------------------
# Echoes — the only trustworthy write confirmation
# ---------------------------------------------------------------------------


def test_observed_echoes_decode():
    for name, hexstr in p.OBSERVED_ECHOES.items():
        rec = p.decode_echo(bytes.fromhex(hexstr))
        assert rec.record == 1, name


def test_echo_for_predicts_what_the_device_actually_sent():
    # This is the check that makes write confirmation meaningful: we can compute the echo
    # we expect *before* sending, then compare.
    assert p.echo_for(p.power(True)).hex().upper() == p.OBSERVED_ECHOES["echo of power on (Delicate)"]
    assert (
        p.echo_for(p.power(False)).hex().upper()
        == p.OBSERVED_ECHOES["echo of power off (work = 0)"]
    )
    # The retired frame still has to predict its own captured echo - it is evidence, and
    # evidence that stops decoding is evidence lost.
    assert (
        p.echo_for(bytes.fromhex(p.RETIRED_OFF["sent"])).hex().upper()
        == p.RETIRED_OFF["echo"]
    )


def test_echo_for_rejects_anything_that_is_not_a_schedule_write():
    with pytest.raises(p.ProtocolError):
        p.echo_for(p.status_query())
    with pytest.raises(p.ProtocolError):
        p.echo_for(p.clock_sync(1, 0, 0, 0))


def test_echo_round_trips_every_field():
    sent = p.schedule(
        enabled=True, days=p.DAY_BITS["mon"] | p.DAY_BITS["fri"], record=3,
        start=(8, 5), end=(10, 45), work=12, pause=90,
    )
    rec = p.decode_echo(p.echo_for(sent))
    assert rec.enabled is True
    assert rec.days == 0x11
    assert rec.record == 3
    assert rec.start == (8, 5)
    assert rec.end == (10, 45)
    assert (rec.work, rec.pause) == (12, 90)


@pytest.mark.parametrize(
    "pkt,match",
    [
        (b"\xa5\xfb\x01", "expected 14 bytes"),
        (bytes.fromhex("A5FA017F010000173B00060078F0"), "not a 0xFB echo"),  # the write, not the echo
        (bytes.fromhex("A5FB017F010000173B00060078FF"), "bad checksum"),
    ],
)
def test_decode_echo_rejects_bad_input(pkt, match):
    with pytest.raises(p.ProtocolError, match=match):
        p.decode_echo(pkt)


# ---------------------------------------------------------------------------
# Live status — speculative, never observed
# ---------------------------------------------------------------------------


def test_live_status_decodes_the_documented_offsets():
    pkt = bytes(range(14)) + bytes([0x03, 0x01, 0x02, 0x00, 0x1E, 0x00, 0x78])
    st = p.decode_live_status(pkt)
    assert st.intensity == 3
    assert st.is_on is True
    assert st.work_status == 2
    assert st.work_remaining == 30
    assert st.pause_remaining == 120
    assert st.raw == pkt


def test_live_status_masks_the_intensity_nibble():
    pkt = bytes(range(14)) + bytes([0xF4, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert p.decode_live_status(pkt).intensity == 4


def test_live_status_refuses_a_short_frame():
    with pytest.raises(p.ProtocolError, match="expected >=21"):
        p.decode_live_status(bytes(20))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_the_echo_is_classified_as_an_echo_and_not_as_live_status():
    echo = bytes.fromhex(p.OBSERVED_ECHOES["echo of power on (Delicate)"])
    assert p.is_echo(echo)
    assert not p.is_live_status(echo)


def test_a_21_byte_frame_is_classified_as_live_status_without_assuming_a_header():
    # Bytes 0-13 are unparsed by the app, so we cannot require A5 here.
    assert p.is_live_status(bytes(21))
    assert not p.is_echo(bytes(21))


def test_the_outbound_write_is_not_mistaken_for_an_echo():
    assert not p.is_echo(p.power(True))


# ---------------------------------------------------------------------------
# Record arbitration — the rule established on hardware 2026-08-09 (#27)
# ---------------------------------------------------------------------------


def test_day_bit_uses_iso_numbering_and_agrees_with_the_day_mask():
    # Monday = 1 through Sunday = 7, the same convention clock_sync() takes and
    # datetime.isoweekday() returns. A rotation here is invisible six days out of seven.
    for iso, name in enumerate(p.DAY_BITS, start=1):
        assert p.day_bit(iso) == p.DAY_BITS[name], name
    assert p.day_bit(_at("thu", "00:00").isoweekday()) == p.DAY_BITS["thu"]


@pytest.mark.parametrize("weekday", [0, 8, -1])
def test_day_bit_refuses_a_weekday_outside_iso_range(weekday):
    with pytest.raises(p.ProtocolError, match="weekday must be 1-7"):
        p.day_bit(weekday)


def test_an_empty_record_matches_nothing():
    # days = 0x00 is how the retired OFF frame expressed "off", and it is the reason that
    # frame fell through instead of stopping the device.
    empty = _record(days=p.NO_DAYS, start=(0, 0), end=(0, 0), work=0, pause=0)
    assert not any(empty.matches_at(now) for now in _EVERY_HOUR_OF_THE_WEEK)


def test_a_record_matches_only_inside_its_own_window():
    mornings = _record(start=(10, 0), end=(12, 0))
    assert not mornings.matches_at(_at("mon", "09:59"))
    assert mornings.matches_at(_at("mon", "10:00"))
    assert mornings.matches_at(_at("mon", "11:30"))
    assert mornings.matches_at(_at("mon", "12:00"))  # inclusive; see matches_at
    assert not mornings.matches_at(_at("mon", "12:01"))


def test_a_record_matches_only_on_the_days_in_its_mask():
    weekdays = _record(days=p.ALL_DAYS & ~(p.DAY_BITS["sat"] | p.DAY_BITS["sun"]))
    assert weekdays.matches_at(_at("fri", "13:00"))
    assert not weekdays.matches_at(_at("sat", "13:00"))
    assert not weekdays.matches_at(_at("sun", "13:00"))


def test_the_all_day_window_covers_the_final_minute_of_the_day():
    # 00:00–23:59 has to match at 23:59 or power-off would lapse for a minute every night.
    assert _record().matches_at(_at("tue", "23:59"))


def test_a_window_running_backwards_over_midnight_is_treated_as_wrapping():
    # An assumption, not a measurement: the app never builds one. Asserted so that changing
    # the assumption is a deliberate act rather than a silent one — see matches_at.
    overnight = _record(start=(22, 0), end=(6, 0))
    assert overnight.matches_at(_at("wed", "23:00"))
    assert overnight.matches_at(_at("wed", "03:00"))
    assert not overnight.matches_at(_at("wed", "12:00"))


def test_matching_is_not_dispensing():
    """The distinction the whole fix rests on. A matching record with work = 0 still wins."""
    off = _record(work=0)
    assert off.matches_at(_at("mon", "13:00"))
    assert not off.is_dispensing_at(_at("mon", "13:00"))


def test_the_lowest_matching_record_id_wins():
    # Established by controlled experiment: record 1 won even when written *before* record 2,
    # which is what rules out last-write-wins.
    records = [_record(2, work=6), _record(1, work=12)]
    assert p.active_record(records, _at("mon", "13:00")).record == 1
    assert p.active_record(reversed(records), _at("mon", "13:00")).record == 1


def test_a_non_matching_record_falls_through_to_the_next_one():
    # 13:00 is outside Mornings (10:00–12:00) and inside Afternoons (12:00–18:00). This is
    # the exact configuration and the exact hour that produced #27.
    active = p.active_record(_APP_SCHEDULES, _at("mon", "13:00"))
    assert active is not None
    assert active.record == 2


def test_no_matching_record_means_nothing_is_running():
    assert p.active_record(_APP_SCHEDULES, _at("mon", "23:00")) is None
    assert p.active_record([], _at("mon", "13:00")) is None


# ---------------------------------------------------------------------------
# DeviceState — "is the diffuser on", which is not "what does record 1 say"
# ---------------------------------------------------------------------------


def test_device_state_reports_on_when_a_lower_record_is_dispensing():
    state = p.DeviceState(
        records={rec.record: rec for rec in _APP_SCHEDULES}, at=_at("mon", "13:00")
    )
    assert state.is_on
    assert state.active.record == 2
    assert not state.is_owned


def test_device_state_off_is_the_bug_that_started_all_this():
    """Clearing record 1 in the presence of app schedules must NOT read as off.

    This is #27 reproduced in code: record 1 zeroed, the app's Afternoons schedule running,
    and a readback of record 1 that truthfully says zero. The old is_on looked only at that
    record and answered "off" while the atomiser was misting.
    """
    cleared = _record(1, days=p.NO_DAYS, start=(0, 0), end=(0, 0), work=0, pause=0)
    records = {rec.record: rec for rec in _APP_SCHEDULES} | {1: cleared}
    state = p.DeviceState(records=records, at=_at("mon", "13:00"))
    assert state.is_on
    assert state.active.record == 2


def test_the_off_frame_reads_as_off_even_with_app_schedules_present():
    """And this is the fix, measured on hardware as #27 phase 4b."""
    off = p.decode_echo(p.echo_for(p.power(False)))
    records = {rec.record: rec for rec in _APP_SCHEDULES} | {1: off}
    state = p.DeviceState(records=records, at=_at("mon", "13:00"))
    assert not state.is_on
    assert state.active.record == 1  # holds the line rather than falling through
    assert state.is_owned


def test_an_owned_record_one_answers_on_its_own():
    # Why the poll still costs one query: nothing behind a matching record 1 is reachable.
    on = p.decode_echo(p.echo_for(p.power(True)))
    for now in _EVERY_HOUR_OF_THE_WEEK:
        state = p.DeviceState(records={1: on}, at=now)
        assert state.is_on, now
        assert state.is_owned, now


def test_a_windowed_record_one_is_not_ours():
    # 13:52 on 2026-08-09: HA reported "on" because record 1 still carried days=0x7F from the
    # app's Mornings entry, while the window had closed at 12:00. The second half of #27.
    state = p.DeviceState(records={1: _APP_SCHEDULES[0]}, at=_at("mon", "13:52"))
    assert not state.is_owned
    assert state.active is None


# ---------------------------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------------------------


def test_describe_days_covers_the_edges():
    assert p.describe_days(p.NO_DAYS) == "none"
    assert p.describe_days(p.ALL_DAYS) == "all"
    assert p.describe_days(p.DAY_BITS["mon"] | p.DAY_BITS["sun"]) == "mon,sun"


def test_describe_echo_names_the_preset_and_the_power_state():
    text = p.describe_echo(bytes.fromhex(p.OBSERVED_ECHOES["echo of power on (Delicate)"]))
    assert "ON" in text
    assert "Delicate" in text
    assert "work=6s" in text

    # OFF because work = 0, not because the days are clear — describe_echo reports whether
    # the record can ever dispense, which is the only clock-free answer available to it.
    text_off = p.describe_echo(bytes.fromhex(p.OBSERVED_ECHOES["echo of power off (work = 0)"]))
    assert "OFF" in text_off
    assert "days=all" in text_off
    assert "work=0s" in text_off
