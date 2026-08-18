#!/usr/bin/env python3
"""Kirri Beacon reachability probe — one command cycle through the ESPHome proxy.

Answers two questions in about three seconds: **is the diffuser reachable, and what is it
set to?** Useful as a smoke test after moving the board or reflashing it, and as the sampler
for the #14 soak test.

**Non-mutating.** The only frame written is the 0xFC status query, so this is safe to run at
any time — it never changes what the diffuser is doing.

It exercises the exact path the HA integration uses: connect through the proxy, subscribe to
notifications, write, wait for the 0xFB echo, unsubscribe, disconnect. Per
docs/research/dossier.md 7.4 the write returning proves nothing — a 0-byte write is accepted
at the ATT layer too — so success here means *the echo came back*.

    python3 tools/kirri_probe.py                 # one cycle
    python3 tools/kirri_probe.py --repeat 20     # sample 20 times, print a summary
    python3 tools/kirri_probe.py --host 10.0.0.5 # if mDNS is not available

Needs `aioesphomeapi` and `pyyaml` (unlike tools/frames.py, which is dependency-free):

    pip install aioesphomeapi pyyaml

Reads `api_encryption_key` and `kirri_mac` from esphome/secrets.yaml, which is gitignored —
no addresses or keys live in this file. Exit status is 0 only if every cycle succeeded.

⚠️ **Do not run this while the Home Assistant integration is loaded.** The proxy has exactly
one bluetooth subscriber slot, and claiming it (which this tool must, to receive its own GATT
responses) displaces Home Assistant until the ESPHome node is restarted. See
docs/research/dossier.md 7.3.
"""
import argparse
import asyncio
import pathlib
import statistics
import sys
import time

# The frame codec ships inside the integration; this tool is a client of it, not a second
# implementation. See custom_components/kirri_beacon/protocol.py.
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "kirri_beacon"),
)

import protocol

DEFAULT_HOST = "kirri-proxy.local"  # the ESPHome node name in esphome/kirri-proxy.yaml
SECRETS = pathlib.Path(__file__).resolve().parent.parent / "esphome" / "secrets.yaml"

# Characteristic handles on the vendor service EDFEC62E-9910-0BAC-5241-D8BDA6932A2F.
# See docs/protocol.md; these are stable across reconnects on this firmware.
HANDLE_NOTIFY = 10
HANDLE_WRITE = 14

ECHO_TIMEOUT_S = 5.0
CONNECT_TIMEOUT_S = 20.0


def load_secrets():
    try:
        import yaml
    except ImportError:
        sys.exit("needs pyyaml:  pip install aioesphomeapi pyyaml")
    if not SECRETS.exists():
        sys.exit(f"{SECRETS} not found - copy secrets.yaml.example and fill it in")
    try:
        with SECRETS.open() as fh:
            s = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        sys.exit(f"{SECRETS} is not valid YAML: {exc}")
    if not isinstance(s, dict):
        sys.exit(f"{SECRETS} is empty or is not a YAML mapping - copy secrets.yaml.example")
    missing = [k for k in ("api_encryption_key", "kirri_mac") if not s.get(k)]
    if missing:
        sys.exit(f"{SECRETS} is missing: {', '.join(missing)}")
    try:
        # Catches the placeholder MAC in secrets.yaml.example being left as-is.
        addr = int(str(s["kirri_mac"]).upper().replace(":", ""), 16)
    except ValueError:
        sys.exit(f"{SECRETS}: kirri_mac is not a MAC address (got {s['kirri_mac']!r})")
    return s["api_encryption_key"], addr


async def one_cycle(client, addr, verbose=True, echo_timeout=ECHO_TIMEOUT_S):
    """Connect, query status, disconnect.

    Returns (echo_bytes|None, connect_seconds|None, echo_seconds|None), where echo_seconds is
    measured from the moment the write returns. That third number is the point of #35: it says
    whether a missing echo is *late* or *absent*, which the integration itself cannot tell you
    because it tears the link down `drain_seconds` after giving up.
    """
    notes = []
    state = {}
    unsub = stop_notify = None
    try:
        t0 = time.monotonic()
        unsub = await client.bluetooth_device_connect(
            addr,
            lambda c, m, e: state.update(connected=c, mtu=m, error=e),
            timeout=CONNECT_TIMEOUT_S,
            feature_flags=255,
            has_cache=False,
            address_type=0,
        )
        if not state.get("connected"):
            if verbose:
                print(f"  connect refused (error={state.get('error')})")
            return None, None, None
        t_connect = time.monotonic() - t0

        stop_notify, _ = await client.bluetooth_gatt_start_notify(
            addr, HANDLE_NOTIFY, lambda handle, data: notes.append(bytes(data)))
        await client.bluetooth_gatt_write(addr, HANDLE_WRITE, protocol.status_query(), True)

        t_write = time.monotonic()
        deadline = t_write + echo_timeout
        while not notes and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if not notes:
            if verbose:
                print(f"  connected, but no echo within {echo_timeout:.1f}s")
            return None, t_connect, None
        return notes[0], t_connect, time.monotonic() - t_write
    finally:
        if stop_notify is not None:
            # Unsubscribe before dropping the link; some firmwares wedge if a client
            # disappears while still subscribed.
            try:
                await stop_notify()
            except Exception:  # noqa: BLE001
                pass
        try:
            await client.bluetooth_device_disconnect(addr)
        except Exception:  # noqa: BLE001
            pass
        if unsub is not None:
            unsub()


async def run(host, repeat, interval, echo_timeout=ECHO_TIMEOUT_S):
    from aioesphomeapi import APIClient

    psk, addr = load_secrets()
    client = APIClient(host, 6053, None, noise_psk=psk)
    try:
        await client.connect(login=True)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"cannot reach the proxy at {host}: {type(exc).__name__}: {exc}")

    # Claim the proxy's single bluetooth subscriber slot. Without this our GATT responses
    # are routed to whoever holds it (normally Home Assistant) and never reach us.
    client.subscribe_bluetooth_le_raw_advertisements(lambda resp: None)
    await asyncio.sleep(1.5)

    latencies, echoes, failures = [], [], 0
    try:
        for i in range(1, repeat + 1):
            label = f"[{i}/{repeat}] " if repeat > 1 else ""
            echo, t_connect, t_echo = await one_cycle(
                client, addr, echo_timeout=echo_timeout)
            if echo is None:
                failures += 1
                print(f"{label}UNREACHABLE")
            else:
                latencies.append(t_connect)
                echoes.append(t_echo)
                # Flag anything the integration's own 5 s deadline would have thrown away.
                late = "  <-- LATE, past ECHO_TIMEOUT_S" if t_echo > ECHO_TIMEOUT_S else ""
                try:
                    print(f"{label}{protocol.describe_echo(echo)}   "
                          f"(connect {t_connect:.2f}s, echo {t_echo:.2f}s){late}")
                except protocol.ProtocolError as exc:
                    failures += 1
                    print(f"{label}unexpected frame {echo.hex().upper()}: {exc}")
            if i < repeat:
                await asyncio.sleep(interval)
    finally:
        await client.disconnect()

    if repeat > 1:
        print(f"\n{repeat - failures}/{repeat} cycles succeeded")
        if latencies:
            print(f"connect latency  min {min(latencies):.2f}s  "
                  f"median {statistics.median(latencies):.2f}s  max {max(latencies):.2f}s")
        if echoes:
            over = sum(1 for e in echoes if e > ECHO_TIMEOUT_S)
            ordered = sorted(echoes)
            p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
            print(f"echo latency     min {min(echoes):.2f}s  "
                  f"median {statistics.median(echoes):.2f}s  p95 {p95:.2f}s  "
                  f"max {max(echoes):.2f}s")
            print(f"echoes past the integration's {ECHO_TIMEOUT_S:.0f}s deadline: "
                  f"{over}/{len(echoes)}")
        if failures:
            # Expected, not alarming: the Beacon does not power up after a mains cut until
            # someone presses the button on its base. See docs/hardware.md.
            print("note: an unreachable diffuser may simply be switched off at the base.")
    return 1 if failures else 0


def positive_seconds(raw):
    """Reject a deadline of zero or less on the command line rather than in the data.

    A non-positive echo deadline expires before the write can possibly be answered, so every
    cycle reports UNREACHABLE — a typo that reads exactly like dead hardware, which is the
    most expensive kind of wrong number this tool can produce.
    """
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0 (got {value:g})")
    return value


def non_negative_seconds(raw):
    """Zero is a legitimate gap between cycles — back-to-back. Negative is always a typo."""
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(f"must not be negative (got {value:g})")
    return value


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"ESPHome proxy (default {DEFAULT_HOST})")
    ap.add_argument("--repeat", type=int, default=1, metavar="N", help="run N cycles")
    ap.add_argument("--interval", type=non_negative_seconds, default=5.0, metavar="S",
                    help="seconds between cycles when repeating (default 5)")
    ap.add_argument("--echo-timeout", type=positive_seconds, default=ECHO_TIMEOUT_S, metavar="S",
                    help=f"how long to wait for the 0xFB echo (default {ECHO_TIMEOUT_S}). "
                         "Raise it above the integration's deadline to find out whether a "
                         "missing echo is late or absent (#35)")
    args = ap.parse_args()
    try:
        return asyncio.run(
            run(args.host, max(1, args.repeat), args.interval, args.echo_timeout))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
