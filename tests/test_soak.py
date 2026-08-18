"""Tests for the soak harness's boot-epoch inference and entity-prefix discovery.

`soak.py` talks to a live Home Assistant box and is otherwise untested on purpose — there is
nothing to assert about a REST client without the box. Two functions are the exception, both
because they are pure functions over a list the box happens to supply. `boot_epochs()` is an
estimator, and #50 was a defect in *which* estimate it chose. `discover_prefix()` is the
replacement for two hardcoded entity ids (#42), and its failure mode is the one the whole
tool exists to prevent: a prefix that is merely *wrong* reports "no samples", which reads
exactly like a clean window — so the tests below pin the loud failures, not just the hit.

The model these fixtures encode: the proxy boots at `_BOOT`, the uptime sensor takes a reading,
and Home Assistant timestamps its **receipt** some staleness later. So every sample yields the
estimate `boot + staleness`, staleness is never negative, and the tightest sample in the window
is the best one.
"""

from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("yaml", reason="soak.py needs PyYAML; use the HA venv")

from tools.soak.soak import (
    BOOT_TOLERANCE_S,
    DEVICE_ANCHOR,
    PROXY_ANCHOR,
    boot_epochs,
    discover_prefix,
    strip_domain,
)

#: The real boot this test's samples are estimating - the one #50 reported two ways.
_BOOT = dt.datetime(2026, 8, 14, 9, 35, 38, tzinfo=dt.timezone.utc)


def _sample(stale_s: float, *, at_s: float = 0.0, boot: dt.datetime = _BOOT) -> dict:
    """One uptime reading received `at_s` after `boot`, having been taken `stale_s` earlier.

    Yields the estimate `boot + stale_s`, which is the whole point: the error is one-sided.
    """
    received = boot + dt.timedelta(seconds=at_s)
    return {"last_changed": received.isoformat(), "state": f"{at_s - stale_s:.3f}"}


def _run(boot: dt.datetime, stalenesses, *, every_s: float = 60.0) -> list[dict]:
    """A node sitting there running, reporting every `every_s` with the given stalenesses."""
    return [
        _sample(stale, at_s=(index + 1) * every_s, boot=boot)
        for index, stale in enumerate(stalenesses)
    ]


def test_reports_the_earliest_estimate_not_the_first_one():
    """The tightest sample wins even when it is not the one the window opened on."""
    epochs = boot_epochs(_run(_BOOT, [50, 3, 41, 58]))

    assert epochs == [(_BOOT + dt.timedelta(seconds=3), 4)]


def test_the_timestamp_does_not_move_when_the_window_starts_later():
    """#50 itself: the same boot reported 19 s apart from two windows minutes apart.

    Dropping leading samples must not change the answer while the tightest one is still in
    range — under the old first-wins rule this moved by a full reporting interval. The
    *count* does drop, and should: it is the evidence behind the timestamp, not the timestamp.
    """
    samples = _run(_BOOT, [50, 3, 41, 58])

    (full, full_n), = boot_epochs(samples)
    (trimmed, trimmed_n), = boot_epochs(samples[1:])

    assert full == trimmed
    assert (full_n, trimmed_n) == (4, 3)


def test_a_later_window_can_only_report_the_boot_later_never_earlier():
    """The honest limit of the fix: min converges from above, it is not window-independent.

    Cutting the tightest sample out of the window costs accuracy — the estimate degrades to
    the next-tightest — but it degrades in one direction only, so a reported boot is always
    an upper bound on the real one.
    """
    samples = _run(_BOOT, [3, 41, 58])

    (full, _), = boot_epochs(samples)
    (without_the_best, _), = boot_epochs(samples[1:])

    assert full == _BOOT + dt.timedelta(seconds=3)
    assert without_the_best == _BOOT + dt.timedelta(seconds=41)
    assert without_the_best > full


def test_a_cluster_is_bounded_from_its_first_estimate_not_its_running_minimum():
    """The reboot count must survive the fix, so the boundary rule may not shift with the min.

    Anchored on the first estimate (50 s) the last sample is 50 s away and stays in the
    cluster; anchored on the running minimum (3 s) it would be 97 s away and split into a
    phantom second boot. One boot, not two.
    """
    epochs = boot_epochs(_run(_BOOT, [50, 3, 100]))

    assert len(epochs) == 1
    assert epochs[0] == (_BOOT + dt.timedelta(seconds=3), 3)


def test_a_real_reboot_still_opens_a_new_epoch():
    second_boot = _BOOT + dt.timedelta(hours=3)
    samples = _run(_BOOT, [40, 12, 55]) + _run(second_boot, [33, 7])

    epochs = boot_epochs(samples)

    assert epochs == [
        (_BOOT + dt.timedelta(seconds=12), 3),
        (second_boot + dt.timedelta(seconds=7), 2),
    ]


def test_jitter_up_to_the_tolerance_is_one_boot():
    """A drift smaller than BOOT_TOLERANCE_S is the sensor's reporting interval, not a reboot."""
    epochs = boot_epochs(_run(_BOOT, [0, BOOT_TOLERANCE_S - 1]))

    assert len(epochs) == 1


def test_unavailable_samples_are_skipped_rather_than_counted():
    """`unavailable` is what the sensor reports *across* a reboot, so it must not back an epoch."""
    samples = _run(_BOOT, [50, 3])
    samples.insert(1, {"last_changed": _BOOT.isoformat(), "state": "unavailable"})

    assert boot_epochs(samples) == [(_BOOT + dt.timedelta(seconds=3), 2)]


def test_no_numeric_samples_is_no_epochs():
    assert boot_epochs([]) == []
    assert boot_epochs([{"last_changed": _BOOT.isoformat(), "state": "unknown"}]) == []


# --------------------------------------------------------------------------------------
# discover_prefix — #42
# --------------------------------------------------------------------------------------

#: A trimmed /api/states, with the shape that made the two hardcoded ids a bug: the proxy and
#: the integration are separate Home Assistant devices carrying unrelated prefixes, and the
#: names are the owner's, not the project's.
_STATES = [
    {"entity_id": "binary_sensor.study_proxy_status"},
    {"entity_id": "sensor.study_proxy_uptime"},
    {"entity_id": "sensor.study_proxy_wifi_signal"},
    {"entity_id": "sensor.study_proxy_kirri_rssi"},
    {"entity_id": "sensor.hall_diffuser_failed_polls"},
    {"entity_id": "sensor.hall_diffuser_echo_timeouts"},
    {"entity_id": "switch.kirri_beacon"},
]


def test_each_device_is_found_by_its_own_anchor():
    """The two prefixes are unrelated — which is the whole reason one --prefix could not do."""
    assert discover_prefix(_STATES, PROXY_ANCHOR, "proxy", "--prefix") == "study_proxy"
    assert discover_prefix(_STATES, DEVICE_ANCHOR, "device", "--device-prefix") == "hall_diffuser"


def test_the_domain_is_stripped_so_one_prefix_serves_both_domains():
    """`binary_sensor.{p}_status` and `sensor.{p}_uptime` share a p, so p carries no domain."""
    assert "." not in discover_prefix(_STATES, PROXY_ANCHOR, "proxy", "--prefix")


def test_a_prefix_pasted_out_of_api_states_keeps_its_meaning():
    """A domain on an override formats to `sensor.sensor.x_…`, which HA 400s mid-report."""
    assert strip_domain("sensor.hall_diffuser") == "hall_diffuser"
    assert strip_domain("binary_sensor.study_proxy") == "study_proxy"
    assert strip_domain("hall_diffuser") == "hall_diffuser"


def test_a_missing_anchor_fails_loudly_and_names_the_override():
    """Not a fallback: a guessed prefix reports "no samples", which reads as a clean window."""
    with pytest.raises(SystemExit) as caught:
        discover_prefix([{"entity_id": "switch.kirri_beacon"}], DEVICE_ANCHOR,
                        "Kirri Beacon integration device", "--device-prefix")

    assert "--device-prefix" in str(caught.value)


def test_two_candidates_are_refused_rather_than_picked_between():
    """Two diffusers on one box. Sorted and listed, because the user has to choose."""
    states = _STATES + [{"entity_id": "sensor.spare_diffuser_failed_polls"}]

    with pytest.raises(SystemExit) as caught:
        discover_prefix(states, DEVICE_ANCHOR, "device", "--device-prefix")

    message = str(caught.value)
    assert "hall_diffuser" in message and "spare_diffuser" in message
    # The candidates are named in the form the flag accepts. An error whose own suggestion
    # fails when pasted is the silent "no samples" this module exists to avoid, one step back.
    assert "sensor." not in message


def test_the_same_prefix_seen_twice_is_still_one_device():
    """History and current state can both appear; a duplicate id is not a second candidate."""
    assert discover_prefix(_STATES + _STATES, PROXY_ANCHOR, "proxy", "--prefix") == "study_proxy"
