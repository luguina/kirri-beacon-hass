#!/usr/bin/env python3
"""Print and verify every frame in docs/protocol.md.

    python3 tools/frames.py

No dependencies — this runs on a bare Python 3 with nothing installed, so the spec can be
checked from anywhere.

**This is a viewer, not an implementation.** The frames come from
`custom_components/kirri_beacon/protocol.py`, which is the single canonical codec: the code
that actually ships is the code that has to be right, so it is the code everything else
points at. A second implementation here would agree with the first by luck rather than by
construction, and the drift would be silent.

The self-check below is a smoke test that the documented table still matches the codec. The
real test suite is `tests/test_protocol.py` (`python3 -m pytest`), which needs pytest.
"""

import pathlib
import sys

# protocol.py lives inside the integration and is deliberately import-free so it can be
# loaded standalone like this, without installing anything or importing Home Assistant.
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "kirri_beacon"),
)

import protocol as p


def main():
    print("Kirri Beacon frames  (docs/protocol.md)\n")

    print("Power  (record 1, all days, 00:00-23:59 — the two differ only in dispense time)")
    print(f"  {'ON  (Delicate 6s/120s)':<26} {p.hexs(p.power(True))}")
    print(f"  {'OFF (work=0)':<26} {p.hexs(p.power(False))}")
    print(f"  {'OFF (retired, pre-#27)':<26} {p.hexs(bytes.fromhex(p.RETIRED_OFF['sent']))}")

    print("\nIntensity presets  (all-day window, record 1)")
    for name, (work, pause) in p.INTENSITY_PRESETS.items():
        print(f"  {name + f'  {work}s/{pause}s':<26} {p.hexs(p.intensity(name))}")

    print("\nOther")
    print(f"  {'status query':<26} {p.hexs(p.status_query())}")
    print(f"  {'clock sync Fri 20:15:00':<26} {p.hexs(p.clock_sync(5, 20, 15, 0))}")

    print("\nExample schedule  (Mon-Fri 08:00-10:00, 12s/120s, record 2)")
    weekdays = (
        p.DAY_BITS["mon"] | p.DAY_BITS["tue"] | p.DAY_BITS["wed"]
        | p.DAY_BITS["thu"] | p.DAY_BITS["fri"]
    )
    print(f"  {p.hexs(p.schedule(enabled=True, days=weekdays, record=2, start=(8, 0), end=(10, 0), work=12, pause=120))}")

    print("\nSelf-check")
    ok = True

    builders = {
        "power on (Delicate)": p.power(True),
        "power off": p.power(False),
        "status query": p.status_query(),
        "clock sync Fri 20:15:00": p.clock_sync(5, 20, 15, 0),
    }
    for name, built in builders.items():
        match = built.hex().upper() == p.KNOWN_GOOD[name]
        ok &= match
        print(f"  [{'ok' if match else 'FAIL'}] {name}")
        if not match:
            print(f"         built    {built.hex().upper()}")
            print(f"         expected {p.KNOWN_GOOD[name]}")
        if not p.validate(built):
            ok = False
            print(f"  [FAIL] checksum validator rejects {name}")

    try:
        p.schedule(enabled=True, days=p.ALL_DAYS, record=0, start=(0, 0), end=(0, 0), work=0, pause=0)
        ok = False
        print("  [FAIL] record 0 was not refused")
    except p.ProtocolError:
        print("  [ok] record 0 refused")

    # The decoder must agree with what the device really sent, and echo_for() must predict it
    # from the outbound frame — that prediction is how a write gets confirmed at runtime.
    for name, expected in p.OBSERVED_ECHOES.items():
        pkt = bytes.fromhex(expected)
        try:
            print(f"  [ok] {name} decodes as  {p.describe_echo(pkt)}")
        except p.ProtocolError as exc:
            ok = False
            print(f"  [FAIL] {name}: {exc}")
    for on, key in (
        (True, "echo of power on (Delicate)"),
        (False, "echo of power off (work = 0)"),
    ):
        predicted = p.echo_for(p.power(on)).hex().upper()
        if predicted != p.OBSERVED_ECHOES[key]:
            ok = False
            print(f"  [FAIL] echo_for() predicted {predicted}, device sent {p.OBSERVED_ECHOES[key]}")
        else:
            print(f"  [ok] echo_for() predicts the observed {key.replace('echo of ', '')} echo")

    # The retired OFF frame is evidence, not a builder target, so it is checked as data: it
    # must still decode, and nothing here may still be producing it. See protocol.RETIRED_OFF.
    if p.echo_for(bytes.fromhex(p.RETIRED_OFF["sent"])).hex().upper() != p.RETIRED_OFF["echo"]:
        ok = False
        print("  [FAIL] the retired OFF frame no longer predicts its own captured echo")
    elif p.power(False).hex().upper() == p.RETIRED_OFF["sent"]:
        ok = False
        print("  [FAIL] power(False) is building the retired record-clearing OFF frame (#27)")
    else:
        print("  [ok] the retired OFF frame decodes, and power(False) no longer builds it")

    print("\nAll frames match the spec." if ok else "\nMISMATCH — docs/protocol.md and the codec disagree.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
