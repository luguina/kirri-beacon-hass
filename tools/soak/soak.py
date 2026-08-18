#!/usr/bin/env python3
"""Install, read and remove the 72-hour soak harness for issue #14.

    python3 tools/soak/soak.py install --curlrc <path-to-curlrc>
    python3 tools/soak/soak.py trigger --curlrc <path-to-curlrc>
    python3 tools/soak/soak.py report  --curlrc <path-to-curlrc> --since <ISO-8601>
    python3 tools/soak/soak.py report  --curlrc <path-to-curlrc> --since <ISO> --until <ISO>
    python3 tools/soak/soak.py remove  --curlrc <path-to-curlrc>

Run `trigger` immediately after `install`: it fires one exercise and tails the result back,
so a typo costs seconds instead of a night of the window. `report` requires `--since`; it
runs to now unless `--until` bounds it, which is how a soak that changed mid-flight gets
reported as two attributable segments rather than one average.

The curlrc is an ordinary curl config file holding both the box and the credential:

    header  = "Authorization: Bearer <long-lived-access-token>"
    # ha-url = http://<your-ha>:8123

The address is written as a **comment** on purpose. A live `url =` line is honoured by curl
as a request in its own right, so `curl -K thisfile https://…/api/states` quietly fetches
*two* URLs and concatenates both bodies — which looks exactly like a corrupt JSON response.
Commented out, curl ignores it and this script still reads it. Keep the file mode 600 and
outside the repo.

`soak-automations.yaml` next to this file is the single source of truth for what gets
installed; this script only translates it to JSON and posts it. Keeping the definition in
one place matters more than usual here, because the copy that runs is on a box you cannot
diff against the repo — if the YAML and the installed automation ever disagree, `install`
is how you make them agree again (it overwrites by id).

Only stdlib plus PyYAML. This has to run from a laptop against a Home Assistant box over
the network, not inside Home Assistant, so it deliberately shares no code with the
integration.

**The token is never printed.** It is read from a curl config file (`header =
"Authorization: Bearer …"`, mode 600) or `$HA_TOKEN`, kept in memory, and sent in a header.
Nothing in this file writes it to stdout, and neither should anything you add.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

import yaml

HERE = pathlib.Path(__file__).resolve().parent
DEFINITIONS = HERE / "soak-automations.yaml"

#: The switch the exercise automation drives, and the entity its logbook entries hang off.
#: Hardcoded rather than discovered, unlike the prefixes below, because it is not a name the
#: harness reads - it is one the harness *requires*: soak-automations.yaml names it sixteen
#: times, so this id is part of the contract. Rename your switch to it before installing.
SWITCH = "switch.kirri_beacon"

#: The integration's own failed-poll counter (#38). Reading it is not optional colour: since
#: #38 a single failed poll is tolerated, so it no longer marks the switch `unavailable` and
#: leaves no other trace - the box has no persistent Home Assistant log. Report the switch's
#: unavailable count alone and a run with the same fault rate as #14 now looks like a fixed
#: box. This is the number that did not change.
FAILED_POLLS = "sensor.{p}_failed_polls"

#: The integration's missing-echo counter (#35), and the same argument one layer down. Since
#: #35 a command whose echo goes astray is retried, so the fault that produced three of #14's
#: 22 write failures now usually ends in a success and leaves no mark on the rate above. This
#: counter is where it goes instead - and unlike FAILED_POLLS it spans reads *and* writes,
#: which is the comparison docs/lab/soak-2026-08.md could only make from a derived poll denominator.
ECHO_TIMEOUTS = "sensor.{p}_echo_timeouts"

#: Everything `report` samples off the ESPHome proxy, and why it is worth sampling.
TELEMETRY = {
    "status": ("binary_sensor.{p}_status", "proxy API link — 'off' means HA lost the node"),
    "uptime": ("sensor.{p}_uptime", "proxy uptime — a drop is a reboot"),
    "wifi": ("sensor.{p}_wifi_signal", "WiFi RSSI drift"),
    "ble": ("sensor.{p}_kirri_rssi", "BLE RSSI drift"),
}

#: Those two groups live on **two different Home Assistant devices** - the ESPHome node and
#: the device this integration creates - so they carry two unrelated entity-id prefixes, and
#: no single --prefix can address both. Worse, neither prefix is knowable in advance: Home
#: Assistant slugs an entity id from the *device* name, which is whatever the owner typed -
#: and reading esphome/kirri-proxy.yaml will not tell you, because renaming the device in the
#: UI afterwards re-slugs every one of its entities and leaves the YAML untouched. That is not
#: hypothetical; it is what happened here. A hardcoded default is therefore not a default, it
#: is one household's name - and the two counters above spent #14 through #35 wearing exactly
#: that, un-overridable, silently reporting "no samples" to anyone else who ran this.
#:
#: What *is* stable is the tail. Entity ids are `<device slug>_<entity slug>`, and the entity
#: half comes from esphome/kirri-proxy.yaml and from this integration, not from the owner. So
#: `report` finds each device by looking for the one entity whose id ends in the anchor below
#: and keeping the head. --prefix / --device-prefix stay as overrides for the case discovery
#: cannot settle: an owner who renamed an entity too, or two diffusers on one box.
PROXY_ANCHOR = "_kirri_rssi"
DEVICE_ANCHOR = "_failed_polls"

#: A boot is only a *new* boot if the inferred epoch moves by more than this. The uptime
#: sensor reports on an interval and Home Assistant timestamps the receipt, not the reading,
#: so the inferred epoch jitters by a sample period even while the node sits there running.
BOOT_TOLERANCE_S = 90


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------

class Client:
    """The smallest thing that can talk to the Home Assistant REST API."""

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._token = token

    def _request(self, method: str, path: str, payload=None):
        req = urllib.request.Request(
            f"{self.url}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode()
        except urllib.error.HTTPError as err:
            # Deliberately does not echo the request headers - see the module docstring.
            raise SystemExit(f"{method} {path} failed: HTTP {err.code} {err.reason}") from err
        except urllib.error.URLError as err:
            raise SystemExit(f"{method} {path} failed: {err.reason}") from err
        return json.loads(body) if body.strip() else None

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, payload):
        return self._request("POST", path, payload)

    def delete(self, path):
        return self._request("DELETE", path)


def read_token(curlrc: str | None) -> str:
    if os.environ.get("HA_TOKEN"):
        return os.environ["HA_TOKEN"]
    if not curlrc:
        raise SystemExit("no token: pass --curlrc or set $HA_TOKEN")
    text = pathlib.Path(curlrc).expanduser().read_text()
    match = re.search(r"Authorization:\s*Bearer\s+([^\s\"']+)", text)
    if not match:
        raise SystemExit(f"no 'Authorization: Bearer …' header found in {curlrc}")
    return match.group(1)


def read_url(arg: str | None, curlrc: str | None) -> str:
    """Resolve the base URL: --url, then $HA_URL, then a `url =` line in the curlrc.

    The curlrc fallback exists so that one short `--curlrc` argument is enough to run this.
    That is not cosmetic: the long form of this command wraps in a terminal, and a wrapped
    line is silently executed by the shell as two commands, the second of which is a bare
    path to a file holding a bearer token. Fewer arguments, fewer ways to fumble the secret.
    `url = "…"` is standard curl config syntax, so the same file still works with `curl -K`.
    """
    if arg:
        return arg
    if os.environ.get("HA_URL"):
        return os.environ["HA_URL"]
    if curlrc:
        text = pathlib.Path(curlrc).expanduser().read_text()
        match = re.search(r"^\s*#?\s*(?:ha-)?url\s*=\s*[\"']?([^\s\"']+)", text, re.MULTILINE)
        if match:
            return match.group(1)
    raise SystemExit("no Home Assistant URL: pass --url, set $HA_URL, or add a 'url =' line "
                     "to the curlrc")


def strip_domain(prefix: str) -> str:
    """Drop a leading `sensor.` / `binary_sensor.` from a prefix.

    Every route to a prefix goes through here, because both routes arrive carrying a domain.
    Discovery reads whole entity ids. A human reads /api/states, where prefixes only ever
    appear with one attached - including in this module's own "pass --device-prefix" errors,
    which is about as direct an invitation to paste one back as there is.

    It has to come off, because the templates supply their own: `sensor.{p}_failed_polls` with
    a domain-bearing p formats to `sensor.sensor.x_failed_polls`. Home Assistant rejects that
    with `HTTP 400`, which at least is loud - but it lands mid-report, after the command
    success rate has already printed, so what the user sees is a half-written report that
    stops on a malformed entity id they did not type in that form.

    Well-formed-but-wrong is the quieter half, and the reason `discover_prefix` refuses to
    guess: the history API answers an unknown-but-valid entity with an empty list, which
    `report` renders as "no samples", which reads exactly like a clean window on a healthy box.

    Splitting on the first dot is exact rather than lenient: an entity id is `<domain>.<object
    id>` and an object id cannot contain a dot, so there is nothing else the leading segment
    could be.
    """
    return prefix.split(".", 1)[-1]


def discover_prefix(states: list[dict], anchor: str, what: str, flag: str) -> str:
    """Recover a device's entity-id prefix from the one entity id ending in `anchor`.

    Fails loudly on both no match and several, and never guesses between candidates. A wrong
    prefix does not raise anything downstream - see `strip_domain` for where that silence
    comes from. So the ambiguity is spent here, where it can still be explained.
    """
    hits = sorted({strip_domain(entity_id[: -len(anchor)]) for entity_id in
                   (state.get("entity_id", "") for state in states)
                   if entity_id.endswith(anchor)})
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"could not find the {what}: no entity id ends in '{anchor}'. Either "
                         f"it is not deployed, or the entity was renamed - pass {flag} with "
                         f"the prefix you see in /api/states")
    # Listed without their domains, i.e. in the form the flag wants. The alternative is an
    # error message whose own suggestions do not work when pasted.
    raise SystemExit(f"could not identify the {what}: {len(hits)} entity ids end in "
                     f"'{anchor}' ({', '.join(hits)}). Pass {flag} to say which one")


# --------------------------------------------------------------------------------------
# History helpers
# --------------------------------------------------------------------------------------

def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str, label: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp, tolerating the `Z` suffix and demanding a timezone.

    A naive timestamp would compare against timezone-aware recorder data and raise deep inside
    a comparison, so it is refused here where the message can say which argument was wrong.
    """
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise SystemExit(f"{label}: not an ISO-8601 timestamp: {value!r}") from err
    if when.tzinfo is None:
        raise SystemExit(f"{label}: needs a timezone, e.g. 2026-08-10T10:36:16Z (got {value!r})")
    return when


def history(
    client: Client, entity: str, start: dt.datetime, end: dt.datetime, *, minimal: bool = True
) -> list[dict]:
    """Every recorded state of `entity` between `start` and `end`.

    Chunked into 24-hour windows because the history endpoint defaults `end_time` to
    start + 1 day. An explicit end_time does widen it, but chunking also keeps a 72-hour
    query off one enormous response, and the seam between chunks is harmless: the first
    sample of each chunk is the state carried in from the previous one.

    `minimal=False` keeps the attributes on every sample. Home Assistant's `minimal_response`
    drops them from all but the first, which is invisible until you try to total an
    attribute across a window and get zero - so any caller reading attributes must turn it
    off. Only worth doing for entities that change rarely; on the switch it would multiply
    the response size for nothing.
    """
    out: list[dict] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=1), end)
        path = (
            f"/api/history/period/{_iso(cursor)}"
            f"?end_time={_iso(stop)}&filter_entity_id={entity}"
            f"{'&minimal_response' if minimal else ''}"
        )
        for series in client.get(path) or []:
            out.extend(series)
        cursor = stop
    # The carried-in sample at each seam duplicates the previous chunk's last state.
    deduped: list[dict] = []
    for sample in out:
        when = sample.get("last_changed") or sample.get("last_updated")
        if deduped and (deduped[-1].get("last_changed") or deduped[-1].get("last_updated")) == when:
            continue
        deduped.append(sample)
    return deduped


def parse_when(sample: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(sample.get("last_changed") or sample["last_updated"])


def unavailable_spans(samples, end: dt.datetime, bad: set[str]) -> list[tuple[dt.datetime, float]]:
    """Every period the entity spent in a `bad` state, as (start, seconds)."""
    spans = []
    opened = None
    for sample in samples:
        when = parse_when(sample)
        if sample["state"] in bad:
            if opened is None:
                opened = when
        elif opened is not None:
            spans.append((opened, (when - opened).total_seconds()))
            opened = None
    if opened is not None:
        spans.append((opened, (end - opened).total_seconds()))
    return spans


def boot_epochs(samples) -> list[tuple[dt.datetime, int]]:
    """Infer when the proxy booted, from `uptime` readings, as (epoch, estimates behind it).

    Counting reboots by watching uptime decrease misses the ones that matter: across a real
    reboot the sensor goes `unavailable` first, so there is no numeric pair to compare. What
    survives that gap is `sample_time - uptime`, which is the boot instant, and which is
    stable to within a reporting interval for as long as the node stays up.

    Every sample yields its own estimate and they disagree, because the node takes the reading
    and Home Assistant timestamps its *receipt*: each estimate is the true boot plus some
    staleness d >= 0. That error is one-sided - an estimate can be late, never early - so the
    **minimum** of a cluster is the maximum-likelihood boot instant. Deliberately not the first
    member, which is whichever sample the window happened to open on and which reported one
    real boot 19 s and 44 s apart across two windows (#50); and deliberately not the mean or
    median, which average in a bias that only points one way. Do not simplify this to an
    average.

    This does not make the timestamp window-independent, and the report should not imply it
    does: more samples can only pull the minimum earlier, so a wider window returns an
    equal-or-earlier boot. What it buys is convergence on the truth from above instead of an
    arbitrary pick, and the returned count says how much evidence is behind each epoch - three
    estimates is a weak timestamp, three hundred is a strong one.

    Cluster boundaries are still measured from each cluster's *first* estimate rather than its
    running minimum, so BOOT_TOLERANCE_S absorbs exactly the jitter it always did and the
    reboot count is unchanged by any of this.
    """
    clusters: list[list[dt.datetime]] = []
    for sample in samples:
        try:
            uptime = float(sample["state"])
        except (TypeError, ValueError):
            continue
        epoch = parse_when(sample) - dt.timedelta(seconds=uptime)
        if not clusters or abs((epoch - clusters[-1][0]).total_seconds()) > BOOT_TOLERANCE_S:
            clusters.append([epoch])
        else:
            clusters[-1].append(epoch)
    return [(min(cluster), len(cluster)) for cluster in clusters]


def numeric(samples) -> list[float]:
    values = []
    for sample in samples:
        try:
            values.append(float(sample["state"]))
        except (TypeError, ValueError):
            pass
    return values


def attribute_values(samples, name: str) -> list[float]:
    """One numeric attribute across a series, skipping samples that do not carry it."""
    values = []
    for sample in samples:
        try:
            values.append(float((sample.get("attributes") or {})[name]))
        except (KeyError, TypeError, ValueError):
            pass
    return values


def _sum_increases(values: list[float]) -> int:
    """Total increase across a monotonic counter, tolerating resets.

    Sums the positive steps rather than subtracting the endpoints, because the counter starts
    at zero again on every integration reload and Home Assistant restart. Last-minus-first
    would report a *negative* window across a restart, and a window that begins after one
    would silently lose everything before it. A reset shows up here as a step down, which
    contributes nothing and lets the count carry on - the only thing lost is the fraction of
    an interval between the last recorded sample and the restart itself.
    """
    total = 0.0
    previous = None
    for value in values:
        if previous is not None and value > previous:
            total += value - previous
        previous = value
    return int(total)


def counted(samples) -> int:
    """Total increase of a counter carried in the entity's state."""
    return _sum_increases(numeric(samples))


def counted_attribute(samples, name: str) -> int:
    """Total increase of a counter carried in an attribute.

    Requires history(..., minimal=False). With `minimal_response` only the first sample keeps
    its attributes, so there are no steps to sum and this returns 0 - a wrong answer that
    looks exactly like a clean window.
    """
    return _sum_increases(attribute_values(samples, name))


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------

def load_definitions() -> list[dict]:
    """Parse the automation definitions, refusing anything this script cannot install.

    The shape checks are not defensive padding. `"id" not in entry` is a *substring* test on
    a string and a *key* test on a mapping, so a YAML file that parses to a list of strings —
    which is what a single missing indent level produces — would sail straight past a naive
    check and fail much later with a KeyError inside `cmd_install`, halfway through writing
    automations to a live Home Assistant.
    """
    definitions = yaml.safe_load(DEFINITIONS.read_text())
    if not isinstance(definitions, list) or not definitions:
        raise SystemExit(f"{DEFINITIONS.name}: expected a non-empty list of automations")
    for index, entry in enumerate(definitions):
        if not isinstance(entry, dict):
            raise SystemExit(
                f"{DEFINITIONS.name}: entry {index} is a {type(entry).__name__}, expected a mapping"
            )
        missing = [key for key in ("id", "alias") if key not in entry]
        if missing:
            raise SystemExit(
                f"{DEFINITIONS.name}: entry {index} is missing {', '.join(missing)}"
            )
    return definitions


def cmd_install(client: Client, args) -> int:
    definitions = load_definitions()
    for entry in definitions:
        client.post(f"/api/config/automation/config/{entry['id']}", entry)
        print(f"  installed  {entry['id']}  {entry['alias']}")

    # A 200 from the config endpoint means "written to automations.yaml", not "loaded and
    # valid". The entity appearing is the part that proves it parsed.
    states = {s["entity_id"]: s for s in client.get("/api/states")}
    missing = []
    for entry in definitions:
        found = [
            s for s in states.values()
            if s["entity_id"].startswith("automation.")
            and s["attributes"].get("id") == entry["id"]
        ]
        if not found:
            missing.append(entry["id"])
        else:
            print(f"  loaded     {found[0]['entity_id']}  state={found[0]['state']}")
    if missing:
        print(f"\nNOT LOADED: {', '.join(missing)} — check the Home Assistant log.")
        return 1
    print("\nInstalled. Mark the soak start time; report with --since that timestamp.")
    return 0


def cmd_remove(client: Client, args) -> int:
    for entry in load_definitions():
        client.delete(f"/api/config/automation/config/{entry['id']}")
        print(f"  removed  {entry['id']}  {entry['alias']}")
    print("\nRemoved. Debug logging stays at its current level until Home Assistant restarts.")
    return 0


def resolve_automation(client: Client, automation_id: str) -> str:
    """Find the entity id Home Assistant gave the automation with this config id.

    It cannot be predicted, and guessing it fails silently. Home Assistant slugs the entity
    id from the *alias*, not from the config `id`, so `kirri_soak_exercise` aliased
    "Kirri soak — exercise (#14)" becomes `automation.kirri_soak_exercise_14`. Worse, calling
    an entity service against an entity that does not exist is not an error — nothing matches
    the target, so nothing runs and the call returns 200. The config `id` is the only stable
    key, and it is carried in the entity's attributes.
    """
    for state in client.get("/api/states") or []:
        if (state["entity_id"].startswith("automation.")
                and state["attributes"].get("id") == automation_id):
            return state["entity_id"]
    raise SystemExit(f"no automation with id '{automation_id}' — run `install` first")


def cmd_trigger(client: Client, args) -> int:
    """Fire one exercise now and show what it wrote to the logbook.

    Worth running the moment the harness is installed. Otherwise the first evidence that the
    automation works at all arrives at 08:00 the next morning, and a typo costs a night of
    the window.
    """
    entity = resolve_automation(client, "kirri_soak_exercise")
    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
    print(f"triggering {entity} (conditions skipped) …")
    client.post(
        "/api/services/automation/trigger",
        {"entity_id": entity, "skip_condition": True},
    )

    seen: set[str] = set()
    # Derived from the automation's own worst case rather than picked round. A single leg can
    # burn 2 x (CONNECT_TIMEOUT_S 20 s + ECHO_TIMEOUT_S 5 s) inside the service call, because
    # the device layer retries once, then up to 45 s more in its wait_template before it gives
    # up on the state following. Two legs 5 s apart is ~195 s. A deadline under that reports a
    # false failure for a run that is still going, which is worse than waiting: it sends you
    # off reading automation traces for a smoke test that was fine.
    # Since #35 that doubling is *reachable* rather than theoretical: the retry used to fire
    # only on a stale link, and a missing echo - the fault this harness actually caught, four
    # times - failed on its first and only attempt at ~7 s. It now costs ~14-20 s. The budget
    # below was already right; only the reason it needs to be that big has changed.
    deadline = time.time() + 300
    while time.time() < deadline:
        path = (
            f"/api/logbook/{_iso(started)}"
            f"?end_time={_iso(dt.datetime.now(dt.timezone.utc))}&entity={SWITCH}"
        )
        for item in client.get(path) or []:
            if item.get("name") != "Kirri soak":
                continue
            key = f"{item.get('when')}{item.get('message')}"
            if key in seen:
                continue
            seen.add(key)
            print(f"  {item.get('when', '?')[11:19]}  {item.get('message')}")
        if any(k for k in seen if "leg2" in k and ("ok leg2" in k or "FAIL leg2" in k)):
            return 0
        time.sleep(5)
    print("\nNo leg-2 result within 300s — check the automation trace in Home Assistant.")
    return 1


def cmd_report(client: Client, args) -> int:
    """Summarise one window of the soak.

    `--until` exists because the window under test is not always the whole soak. Anything
    that changes the device's *behaviour* without changing the code, the transport or the RF
    environment - raising the intensity, say - does not invalidate a run, but it does mean the
    numbers either side of it answer different questions. Averaging across the change would
    quietly attribute a shift in failure rate to whichever cause the reader already believed.
    Bounding the window is how each half stays attributable.
    """
    start = _parse_ts(args.since, "--since")
    # `is not None`, not truthiness: `--until ""` is a flag that was *given*, and the only
    # honest responses are to parse it or to reject it. Treating it as absent would silently
    # widen the window to now - which is the one failure this whole command exists to prevent,
    # and it would look like a perfectly good report. `--until "$END"` with an unset $END is
    # not a hypothetical way to reach it.
    bounded = args.until is not None
    end = _parse_ts(args.until, "--until") if bounded else dt.datetime.now(dt.timezone.utc)
    if end <= start:
        raise SystemExit(f"--until ({_iso(end)}) must be after --since ({_iso(start)})")
    hours = (end - start).total_seconds() / 3600
    # Says how long the window is, and nothing about how much soak is left. It used to read
    # "of 72", which silently assumed --since was the soak's start; the moment --until made
    # mid-run segments possible that assumption produced "0.1 h elapsed of 72" for a segment
    # opening 21 hours in. A tool that cannot know a fact should not report it.
    window = f"{hours:.1f} h window" + ("" if bounded else ", open-ended to now")
    print(f"Kirri soak — {_iso(start)} to {_iso(end)}  ({window})\n")

    # One /api/states call answers both prefixes, and only when at least one is still to be
    # resolved - so `--prefix X --device-prefix Y` needs no read at all, which is what a box
    # with renamed entities is left with.
    states = ([] if args.prefix and args.device_prefix else client.get("/api/states")) or []
    proxy_prefix = strip_domain(args.prefix) if args.prefix else discover_prefix(
        states, PROXY_ANCHOR, "ESPHome proxy device", "--prefix")
    device_prefix = strip_domain(args.device_prefix) if args.device_prefix else discover_prefix(
        states, DEVICE_ANCHOR, "Kirri Beacon integration device", "--device-prefix")
    echo_timeouts = ECHO_TIMEOUTS.format(p=device_prefix)
    failed_polls = FAILED_POLLS.format(p=device_prefix)

    # ---- command success rate, from the logbook entries the exercise automation wrote ----
    entries = []
    cursor = start
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=1), end)
        path = f"/api/logbook/{_iso(cursor)}?end_time={_iso(stop)}&entity={SWITCH}"
        for item in client.get(path) or []:
            if item.get("name") == "Kirri soak":
                entries.append(item)
        cursor = stop

    tally = {"attempt leg1": 0, "ok leg1": 0, "FAIL leg1": 0,
             "attempt leg2": 0, "ok leg2": 0, "FAIL leg2": 0, "skip": 0}
    latencies: list[float] = []
    incidents: list[str] = []
    for item in entries:
        message = item.get("message", "")
        for key in tally:
            if message.startswith(key):
                tally[key] += 1
                break
        found = re.search(r" in ([\d.]+)s", message)
        if found:
            latencies.append(float(found.group(1)))
        if message.startswith("FAIL") or message.startswith("skip"):
            incidents.append(f"{item.get('when', '?')[:19]}  {message}")

    attempts = tally["attempt leg1"]
    passed = tally["ok leg1"]
    failures = tally["FAIL leg1"]
    # An attempt that logged neither `ok` nor `FAIL` never reached its verification step, so
    # the run died mid-sequence. That is the signature of `continue_on_error` failing to
    # swallow the integration's HomeAssistantError, and it matters more than it looks: an
    # aborted run also skips leg 2, which leaves the diffuser inverted until the next one.
    # Counted separately rather than folded into either column, because scoring it as a pass
    # (attempts - failures) would hide exactly the case the harness exists to catch.
    aborted = attempts - passed - failures
    print("Command success rate")
    if attempts:
        print(f"  leg 1 (verified writes)  {passed}/{attempts}  = {100 * passed / attempts:.1f}%")
    else:
        print("  leg 1 (verified writes)  no attempts recorded yet")
    print(f"  leg 2 (restore)          {tally['ok leg2']} ok, {tally['FAIL leg2']} left inverted")
    print(f"  skipped (unavailable)    {tally['skip']}")
    if aborted:
        print(f"  ABORTED mid-run          {aborted}  — logged an attempt but never a result")
    if latencies:
        print(f"  round-trip latency       min {min(latencies):.2f}s  "
              f"median {statistics.median(latencies):.2f}s  max {max(latencies):.2f}s")

    # The rate above stopped being the fault rate when #35 shipped: a missing echo is now
    # retried, so the fault that produced every one of #14's write failures usually ends in a
    # pass. Printed here rather than under Availability because it counts *commands* - reads
    # and writes together, which is the population the #14 soak had to reason about indirectly.
    # Attributes carry the outcome, so this is the one query that cannot use minimal_response.
    echo_samples = history(client, echo_timeouts, start, end, minimal=False)
    if echo_samples:
        # `recovered`/`failures` are scoped to echo-driven retries by the integration, which
        # is what lets them be printed on this line at all - a stale-link retry counted into
        # the same pair would show up here as a rescued echo that never happened.
        rescued = counted_attribute(echo_samples, "recovered")
        lost = counted_attribute(echo_samples, "failures")
        print(f"  missing echoes           {counted(echo_samples)}  "
              f"({rescued} rescued by the retry, {lost} still failed)")
        # The split the #14 soak had to argue for from overlapping confidence intervals over a
        # derived poll denominator. Printed as counts, not rates: the read denominator is
        # still not measurable (a successful no-op poll leaves no recorder row), so a rate
        # here would be the same inference wearing a more confident face.
        print(f"    on reads {counted_attribute(echo_samples, 'on_reads')}  "
              f"on writes {counted_attribute(echo_samples, 'on_writes')}")
        other = counted_attribute(echo_samples, "other_retries")
        if other:
            # A different, older fault - a stale link or a transport timeout. Printed only
            # when non-zero so it cannot be mistaken for part of the count above, but printed
            # when it happens so the retry path is never busier than the report admits.
            print(f"    plus {other} retr{'y' if other == 1 else 'ies'} from a stale link or "
                  f"transport timeout, not a missing echo")
    else:
        # Not folded into a zero, for the same reason as the failed-poll line below: a
        # missing counter and a clean window read identically once both print "0".
        print(f"  missing echoes           no samples - {echo_timeouts} did not exist in "
              f"this window. Either it predates #35 or the entity id differs; check /api/states")

    # ---- availability ----
    print("\nAvailability")
    switch_samples = history(client, SWITCH, start, end)
    switch_gaps = unavailable_spans(switch_samples, end, {"unavailable", "unknown"})
    print(f"  {SWITCH}: {len(switch_gaps)} unavailable period(s)"
          f"{', longest %.0fs' % max(s for _, s in switch_gaps) if switch_gaps else ''}")

    # The line above stopped being the read-failure count when #38 shipped: it now only shows
    # the failures that were *not* tolerated. Printed together so the pair cannot be misread.
    failure_samples = history(client, failed_polls, start, end)
    if failure_samples:
        print(f"  failed polls: {counted(failure_samples)}"
              f"  (a tolerated miss leaves no mark on the line above)")
    else:
        # Not folded into a zero. A missing counter and a clean window read identically once
        # they are both printed as "0", and this tool exists to tell those two apart.
        print(f"  failed polls: no samples - {failed_polls} did not exist in this window. "
              f"Either it predates #38 or the entity id differs; check /api/states")

    entities = {name: (tpl.format(p=proxy_prefix), why) for name, (tpl, why) in TELEMETRY.items()}

    status_samples = history(client, entities["status"][0], start, end)
    status_gaps = unavailable_spans(status_samples, end, {"off", "unavailable", "unknown"})
    brief = [s for _, s in status_gaps if s < 5]
    real = [s for _, s in status_gaps if s >= 5]
    print(f"  proxy API link: {len(status_gaps)} drop(s) — {len(brief)} sub-5s reconnects, "
          f"{len(real)} longer{', longest %.0fs' % max(real) if real else ''}")

    # ---- proxy reboots ----
    uptime_samples = history(client, entities["uptime"][0], start, end)
    epochs = boot_epochs(uptime_samples)
    print(f"\nProxy reboots: {max(0, len(epochs) - 1)} during the window "
          f"({len(epochs)} boot epoch(s) seen)")
    for epoch, backing in epochs:
        # The count is printed because the timestamp is only as good as the tightest estimate
        # behind it: one sample can be a whole reporting interval late, hundreds cannot all be.
        print(f"  booted {epoch.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}"
              f"  (earliest of {backing} estimate{'' if backing == 1 else 's'})")

    # ---- RSSI drift ----
    print("\nSignal")
    for key, label in (("wifi", "WiFi RSSI"), ("ble", "BLE RSSI")):
        values = numeric(history(client, entities[key][0], start, end))
        if values:
            print(f"  {label:<10} min {min(values):.0f}  mean {statistics.fmean(values):.1f}  "
                  f"max {max(values):.0f} dBm  (n={len(values)})")
        else:
            print(f"  {label:<10} no samples")

    # ---- the incident list is the part a human actually reads ----
    print(f"\nIncidents ({len(incidents)})")
    for line in incidents:
        print(f"  {line}")
    if not incidents:
        print("  none")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("install", "trigger", "remove", "report"))
    parser.add_argument("--url", help="Home Assistant base URL (or $HA_URL)")
    parser.add_argument("--curlrc", help="curl config file holding the bearer token (or $HA_TOKEN)")
    parser.add_argument("--since", help="report: ISO-8601 window start, e.g. 2026-08-09T13:35:00Z")
    parser.add_argument("--until", help="report: ISO-8601 window end; defaults to now. Use it to "
                                        "bound a segment when something changed mid-soak")
    parser.add_argument("--prefix",
                        help="report: ESPHome proxy entity-id prefix; discovered from "
                             f"/api/states via '{PROXY_ANCHOR}' when omitted")
    parser.add_argument("--device-prefix",
                        help="report: entity-id prefix of the device this integration creates "
                             "- a different device, so usually a different prefix; discovered "
                             f"via '{DEVICE_ANCHOR}' when omitted")
    args = parser.parse_args()

    if args.command == "report" and not args.since:
        raise SystemExit("report needs --since <ISO-8601 window start>")

    client = Client(read_url(args.url, args.curlrc), read_token(args.curlrc))
    commands = {
        "install": cmd_install,
        "trigger": cmd_trigger,
        "remove": cmd_remove,
        "report": cmd_report,
    }
    return commands[args.command](client, args)


if __name__ == "__main__":
    sys.exit(main())
