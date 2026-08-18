"""Constants for the Kirri Beacon integration.

Protocol constants live in protocol.py, which is deliberately free of Home Assistant
imports. This file is for the Home-Assistant-facing bits: the domain, the GATT addresses we
talk to, and the tunables exposed in the options flow.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "kirri_beacon"
MANUFACTURER: Final = "Scent Australia Home"
MODEL: Final = "Beacon (SAH5910)"

# --- GATT -------------------------------------------------------------------------------
# Resolve by UUID, never by handle: handles are only guaranteed stable between
# service-changed events. docs/protocol.md records handles 10/14 for debugging tools, but
# the integration must not depend on them.
SERVICE_UUID: Final = "edfec62e-9910-0bac-5241-d8bda6932a2f"
WRITE_UUID: Final = "0783b03e-8535-b5a0-7140-a304d2495cba"
NOTIFY_UUID: Final = "0783b03e-8535-b5a0-7140-a304d2495cb8"

#: The only thing this device puts on air. Shared with a large population of cheap BLE
#: devices, and it does not even implement it - so discovery must confirm SERVICE_UUID after
#: connecting. See docs/research/dossier.md 6.1.
ADVERTISED_SERVICE_UUID: Final = "0000fff0-0000-1000-8000-00805f9b34fb"

# --- Config / options -------------------------------------------------------------------
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_DRAIN_SECONDS: Final = "drain_seconds"

#: Slow on purpose. Home Assistant is the sole controller (ADR-008), so out-of-band changes
#: are rare; the real state signal is the echo returned by every write, and the real
#: availability signal is the advertisement. This is only a safety net.
DEFAULT_POLL_INTERVAL_S: Final = 900
MIN_POLL_INTERVAL_S: Final = 60
MAX_POLL_INTERVAL_S: Final = 86400

#: Consecutive failed polls the entities survive before reporting `unavailable` (#38). One
#: failed poll used to cost a full interval of unavailability - 900 s for a miss the next poll
#: recovered - which the #14 soak measured 13 times in 72 hours. See ADR-012 for why holding a
#: stale reading is honest on this device, and coordinator.PollHealth for the counting.
#:
#: Deliberately a constant and not an option. The question a user might tune it for - "is the
#: link flaky enough to need more slack" - is answered by the failed-poll counter this ships
#: with, not by guessing at a number, and an option costs a strings entry, a translation and a
#: reload path for a value nobody has evidence to pick.
TOLERATED_POLL_FAILURES: Final = 1

#: How long to hold the BLE link open after a command, to absorb a follow-up before paying
#: for another connect. One UI gesture routinely produces two writes (change intensity, then
#: toggle power), and a full connect cycle costs 3-5 s (docs/hardware.md).
DEFAULT_DRAIN_SECONDS: Final = 2.0
MAX_DRAIN_SECONDS: Final = 30.0

# --- Intensity bounds ---------------------------------------------------------------------
# Intensity on this model *is* the work/pause ratio - there is no intensity register - so
# these are the bounds of the two number entities. Both fields are real uint16s on the wire
# (protocol.MAX_SECONDS), so everything below is a judgement about what is sensible to offer,
# not a protocol limit. They are separated from the protocol constants for exactly that reason.

#: The dispense range the vendor's own app suggests. Kept as-is: it is the only range the
#: hardware is known to have been designed around, and the presets (6/12/18/24 s) sit in it.
MIN_DISPENSE_S: Final = 3
MAX_DISPENSE_S: Final = 48

#: The floor is a hardware guard, not a protocol one. ``pause = 0`` means the atomiser never
#: rests; the app cannot produce it, this device has never been asked to do it, and finding out
#: what it does to the unit is not an experiment worth running on the only one we have.
MIN_PAUSE_S: Final = 10
#: The ceiling is usability - an hour between bursts is already indistinguishable from off.
MAX_PAUSE_S: Final = 3600

# --- Timing -----------------------------------------------------------------------------
# Presence is not timed here on purpose. Home Assistant's own tracker knows the device's
# advertising interval and applies the right grace period; an earlier hand-rolled timeout was
# defeated by habluetooth suppressing identical advertisements. See coordinator.device_present.

#: Delay between steps of the documented connect sequence (docs/protocol.md).
SEQUENCE_DELAY_S: Final = 0.3

#: How long to wait for the A5FB echo that confirms a write.
ECHO_TIMEOUT_S: Final = 5.0

CONNECT_TIMEOUT_S: Final = 20.0

#: Connection attempts per command, passed to bleak_retry_connector. The library default is
#: 4, and our own retry-once doubles it - which was observed tying up the proxy's connection
#: slots for ~4 minutes when a config flow was pointed at a device that was not a Beacon.
DEFAULT_MAX_ATTEMPTS: Final = 3

#: Validation only needs to know "is this a Beacon, yes or no". Failing fast matters more
#: than succeeding through a flaky link, because the answer is usually no: the 0xFFF0 matcher
#: surfaces unrelated devices, and each one a user clicks costs real proxy connection slots
#: that the actual diffuser then cannot get.
VALIDATE_MAX_ATTEMPTS: Final = 1

#: The clock sync is unacknowledged, and since on-device schedules were dropped (#17,
#: ADR-009) nothing in this integration reads the device's RTC at all. It is a courtesy now,
#: not a requirement, so it need not run on every connect. Re-sent at most this often.
CLOCK_SYNC_INTERVAL_S: Final = 3600.0
