# Decision log

ADR-style. One entry per decision that would otherwise get re-argued. Record the
**reasoning**, not just the verdict — including what would change the answer.

Status values: `accepted` · `superseded` · `pending`.

---

## ADR-001 — Bridge BLE over WiFi with an ESPHome `bluetooth_proxy`

**Status:** accepted · **Date:** 2026-08-08

**Context.** The diffuser sits three floors from the Home Assistant box. HA's own
Bluetooth adapter cannot reach it — BLE at that range through two floors of house is not
a tuning problem, it's a physics problem.

**Decision.** Put an ESP32 running ESPHome's `bluetooth_proxy` next to the diffuser and
let it carry GATT traffic over WiFi. All protocol logic stays in HA; the ESP32 is
transparent.

**Why this and not an ESP32 that controls the diffuser directly?** Because a transparent
proxy is reusable and dumb. If the protocol understanding changes, the change is a
software update in HA — no reflashing a board three floors up. It also means any *other*
BLE device in that part of the house becomes reachable for free.

**Verified, not assumed:** `scent-assistant`'s `device.py:286` resolves the device via
`bluetooth.async_ble_device_from_address(hass, addr, connectable=True)`, which is exactly
the call that routes through a proxy. The topology works at runtime.

**Confirmed on hardware 2026-08-08 (#4).** The proxy forwards the Kirri's advertisements and
completes a full GATT connect + service discovery through it, MTU 251, clean teardown with no
leaked connection slot. The topology is no longer an assumption.

**What would change this:** if #5 shows the proxy cannot hold a stable link at the real
install site, or if the Kirri turns out to require a persistent connection that a proxy
handles badly.

---

## ADR-002 — Decide the control implementation *after* decoding, not before

**Status:** accepted · **Date:** 2026-08-08

**Context.** Two viable targets: ESPHome YAML (`ble_client` + `ble_write`) on the ESP32
itself, or a Python integration in HA. Each is defensible.

**Decision.** Do not choose now. Flash the ESP32 with a **stock `bluetooth_proxy`** —
which is needed under either option — and defer the choice to a **decision gate (#9)**
with written criteria, once the protocol is actually known.

**Why.** The honest reason the choice can't be made yet is that the deciding evidence
doesn't exist. My initial argument for one route leaned on iteration speed, which is a
weaker criterion than it sounded: protocol validation happens on a laptop next to the
diffuser either way, so it doesn't discriminate between the targets.

**Worth being explicit about a near-miss:** the "3 floors away" constraint does **not**
choose between ESPHome and Python. Both put an ESP32 next to the diffuser. It only rules
out using HA's own Bluetooth adapter. Constraints that feel decisive often aren't — check
whether a constraint actually separates the options before letting it pick for you.

**What decides it at the gate:** whether `scent-assistant` works as-is (#6/#7), whether
app coexistence holds (#8), and whether anything needs to be fixed upstream (#10/#11).

---

## ADR-003 — Use the DFRobot FireBeetle 2 ESP32-E already owned

**Status:** superseded by [ADR-010](#adr-010--the-proxy-is-a-seeed-xiao-esp32c6) · **Date:** 2026-08-08

**Context.** Smaller boards exist and the enclosure will eventually matter.

**Decision.** Use the FireBeetle in hand. Revisit size only once the system works.

**Why.** The proxy's workload is trivial; the constraint that actually matters is radio
quality, and the FireBeetle's is fine. Buying hardware to shave centimetres before the
protocol is even confirmed optimises the wrong thing at the wrong time.

**Amendment (2026-08-08).** "Radio quality" above was too coarse, and the coarseness
produced a bad rejection in `hardware.md` that has since been corrected. This device has
**two** radio links: BLE to the diffuser (~1 m — antenna quality is close to irrelevant)
and WiFi to the AP (the long link, and the only one where a weak antenna bites). Any board
argument that cites "range" has to say *which link*, or it isn't an argument. The verdict
is unchanged — but the honest reason to keep the FireBeetle is that it's owned and
sufficient, not that the alternatives are disqualified.

**Amendment 2 (2026-08-08, #5) — measured, and this decision's own exit condition has now
fired.**
Both links were sampled at the real install site: BLE **−56 dBm**, WiFi **−26 dBm**, and a
GATT connection establishes and tears down cleanly. That kills the last live argument for
preferring a larger board here — a chip antenna's ~6 dB penalty on a −26 dBm link is
immaterial.

So the "revisit size only once the system works" condition has been met, and a **Seeed XIAO
ESP32C6** has been ordered to replace it (2026-08-08; details and pre-purchase verification
in [`lab/soak-2026-08.md`](lab/soak-2026-08.md)). The decision's *reasoning* holds up — don't buy hardware
before the protocol is confirmed — but note what it cost: the analysis behind it ran well
ahead of the one 90-second measurement that would have resolved it. Measure the link first
next time.

**~~Status deliberately stays `accepted`, not `superseded`.~~** The FireBeetle is still the
board in service; the C6 has been ordered but not received, flashed or proven. This ADR
becomes `superseded` when the C6 is actually running as the proxy — not when a replacement
is bought. Flipping it early would make the log claim a hardware change that hasn't
happened, which is exactly the kind of thing a decision log exists to prevent.

**That condition fired on 2026-08-09.** The C6 was received, compiled, flashed, sited and
measured, and is now the board in service — so the status above is now `superseded` by
[ADR-010](#adr-010--the-proxy-is-a-seeed-xiao-esp32c6). The gate worked exactly as intended:
between ordering and this line the log never once claimed a swap that had not happened, and
the swap turned out to carry a defect ([the RF switch](hardware.md#-on-a-xiao-esp32c6-the-rf-switch-is-mandatory))
that an early flip would have recorded as a clean success.

Rejected alternatives and their reasons are in [`hardware.md`](hardware.md#choosing-a-board);
what this project actually had on the shelf is in [`lab/soak-2026-08.md`](lab/soak-2026-08.md).

---

## ADR-004 — Local BLE only; the Aroma-Link cloud API is out of scope

**Status:** accepted · **Date:** 2026-08-08

**Decision.** Use `scent-assistant` in **BLE** mode, not Cloud mode.

**Why.** Local control has no vendor dependency, no account, no outage, and no latency to
Shenzhen. It also happens to be the only mode where fan control exists at all. The cloud
path is a genuine fallback if BLE proves impossible — but it inverts the point of the
project, so it stays out until it's the last option.

---

## ADR-005 — Scope: power, intensity, work/pause, schedules. LED excluded.

**Status:** accepted · **Date:** 2026-08-08

**Decision.** In scope: on/off · mist/fan intensity · work/pause interval timing ·
schedules and timer windows. Out: LED/lamp, cloud API.

**Beacon amendment (same date).** Kirri's own documentation says strength is set by
*"the run and pause times"* — the Beacon has **no separate intensity control**. So
"intensity" in the scope statement is delivered *by* work/pause duration, and the
`Intensity` / `Level` / `Fan` entities from `scent-assistant` are expected to be inert on
this model. Confirm in #7, then hide them rather than debug them.

---

## ADR-006 — The phone app must keep working (hard requirement)

**Status:** superseded by [ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller) · **Date:** 2026-08-08

**Decision.** Any solution that breaks the Kirri phone app is rejected, regardless of how
well it works from HA.

**Why it's achievable.** `scent-assistant` connects only to send a command and disconnects
after a short idle window (`device.py:455-494`), rather than holding the link. That's the
correct design for a device that accepts one connection at a time.

**~~The unresolved risk.~~** Kirri documents *"hold the Bluetooth button for 5 seconds to
connect."* If the Beacon bonds to a single central and gates new connections behind that
button, HA and the app **cannot** share it — and no implementation detail fixes that. It
would be a device limitation, and the honest options become "HA only" or "app only".
That's a decision for the maintainer, not a problem to engineer around quietly.

**Update 2026-08-08 (#4) — this risk is effectively dead.** Two independent findings kill it:
the device **does not bond** (#3), and it **accepted a GATT connection through the proxy with
no button pressed** — proven by a *failed* attempt whose log shows `param->open.status == OK`
before our own code tore it down. Details in
[`research/dossier.md` §7.2](research/dossier.md#72-the-physical-bluetooth-button-is-not-required-).
The "HA only or app only" fork is off the table.

**Settled by:** #3 (first signal) → #4 (connection proven without the button) → #8 (the
remaining question: does it survive a power-cycle, and can app and HA alternate cleanly).

**Outcome 2026-08-08 (#8):** the requirement itself was withdrawn before the coexistence
question could be answered — see [ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller).
The BLE half of this ADR's fear is confirmed dead; a different, unrelated limitation was found
in its place (mains recovery needs a physical button — [`hardware.md`](hardware.md#power-cycle-behaviour)).

---

## ADR-008 — The phone app requirement is withdrawn; HA becomes the sole controller

**Status:** accepted · **Date:** 2026-08-08 · **Supersedes [ADR-006](#adr-006--the-phone-app-must-keep-working-hard-requirement)** · **Closes #8**

**Context.** [ADR-006](#adr-006--the-phone-app-must-keep-working-hard-requirement) made "the
Kirri phone app must keep working" a hard requirement, and #8 existed to enforce it. When #8
was run, **the phone app would not connect to the diffuser at all** — independently of us.

**Decision (the maintainer, 2026-08-08).** Drop the requirement. HA becomes the sole controller.

**Why this is safe.** The app was insurance against losing control of the device, and that
insurance is no longer needed: we can write any schedule record ourselves, including OFF and
any work/pause pairing, so the diffuser can always be recovered from software. Giving up the
app costs a convenience, not a capability.

**Be precise about what was and was not established, because it is easy to misread this as a
pass:**

| | |
|---|---|
| App-and-HA coexistence | **Never tested.** Tests 3–6 of #8 all require a working app connection, so none of them ran. This is *withdrawn*, not *verified*. |
| Why the app cannot connect | **Not diagnosed.** The requirement was dropped before the cause was found. |
| Are *we* the cause? | **No.** The diffuser accepted us 5/5 while the app was failing — connect in ~1 s, correct `A5FB` echo, clean release. We never held its radio for more than ~230 ms per command. |
| ADR-006's actual fear (bonds to one central, gates BLE behind the 5-second button hold) | **Dead.** Confirmed again after a power-cycle: GATT connected in 0.85 s with nobody touching the device. |

**What would reopen this:** wanting the app back. If that happens the work is a diagnosis, not
a redesign — our connect-on-demand behaviour already leaves the device free ~99% of the time,
so there is nothing in our design to undo first.

**The genuinely bad news came from elsewhere in #8.** The Beacon does **not** power up after
mains is restored; it needs a press of the physical on/off button on its base, and no software
or smart plug substitutes for that. That is a device limitation, documented prominently in
[`hardware.md`](hardware.md#power-cycle-behaviour), and it shapes #13's reconnect policy and
adds an unreachable-alert to #15.

---

## ADR-007 — Build our own integration in Python; the ESP32 stays a stock proxy

**Status:** accepted · **Date:** 2026-08-08 · **Resolves the #9 decision gate**

**Context.** The gate ([ADR-002](#adr-002--decide-the-control-implementation-after-decoding-not-before))
deliberately deferred this until the protocol was known. It now is — recovered from the
vendor's own source and confirmed on the device.

**What the evidence decided.**

*Reuse `scent-assistant`?* **No.** It implements six BLE diffuser families and the Beacon is
none of them — different service, different characteristics, different framing, different
checksum. There is nothing to reuse but scaffolding. This was the front-runner all morning and
the protocol killed it outright.

*ESPHome YAML on the ESP32, or Python in HA?* **Python**, and this is now a clear call rather
than a coin-flip. The deciding factor is that **the protocol is stateful and compositional**:
there is no power command — "turn on" means writing a full schedule record that embeds the
*currently selected* work/pause values. Expressing that in ESPHome lambdas means threading
state between entities in YAML, plus hand-rolling big-endian uint16 packing, sum-mod-256
checksums, and parsing two notification formats. All of that is ordinary Python and awkward
YAML.

Note this is the opposite of the criterion I first reached for (iteration speed), which
[ADR-002](#adr-002--decide-the-control-implementation-after-decoding-not-before) already
flagged as weaker than it sounded. The real criterion turned out to be *protocol shape*, and
it wasn't knowable until the protocol was.

**Decision.** ESP32 runs stock `bluetooth_proxy` (unchanged from [ADR-001](#adr-001--bridge-ble-over-wifi-with-an-esphome-bluetooth_proxy)).
All protocol logic lives in a custom HA integration.

**Deliberately not doing (for now):** contributing a Beacon handler upstream to
`scent-assistant`. It would be good citizenship and the protocol doc is written for it — but
it makes our timeline depend on a maintainer's review. Ship ours first, offer it after.

---

## ADR-009 — Scheduling lives in Home Assistant, not in the device's schedule records

**Status:** accepted · **Date:** 2026-08-09 · **Amends [ADR-005](#adr-005--scope-power-intensity-workpause-schedules-led-excluded)** · **Rewrites #17**

**Context.** [ADR-005](#adr-005--scope-power-intensity-workpause-schedules-led-excluded) put
"schedules and timer windows" in scope, and #17 was to deliver them by exposing the device's
four schedule records as Home Assistant entities. The arbitration work of 2026-08-09
([`protocol.md`](protocol.md) → *Record arbitration*, #28) makes that unbuildable.

**Decision.** Schedules are **Home Assistant automations driving the existing switch and
intensity entities**. The integration writes record 1 and nothing else. Records 2–4 are never
written — eventually surfaced read-only as diagnostics, so the app's leftovers are at least
legible from HA.

**Why. Three reasons, in descending order of force.**

**1. On-device schedules and a reliable *off* are mutually exclusive.** The device runs the
first *enabled* record, by record id, whose day mask **and** window match now. So the only way
to make *off* stick is a record 1 that always matches — all days, `00:00`–`23:59` — and
therefore permanently outranks records 2–4. That is exactly #27's fix. An always-matching
record 1 means **records 2–4 can never fire.** #17's original "reserve record 1 for power,
expose 2–4 as user schedules" was never a trade-off between two workable designs; it was a
contradiction. The property that makes *off* work is the property that kills those records.

**2. The records are not ours to hold.** The phone app re-pushes its three default schedules
into records 1–3 on *every connect*, not only on save. Anything we wrote there is silently
overwritten the moment somebody opens the app, with no event to tell Home Assistant — so HA
would go on displaying a schedule the device is not running. The same class of failure as #27,
only quieter.

**3. They run on a clock we cannot verify.** The device evaluates schedules against its own
RTC. We send `0xFD` and it has **never once acknowledged it**; there is no confirmation we can
set that clock at all, only circumstantial evidence that it is roughly right. And the Beacon is
mains-powered with **no battery** ([`hardware.md`](hardware.md#power-cycle-behaviour)) —
settings survive an outage from non-volatile storage, but a clock with no battery cannot tick
through one. After any power cut the device's idea of time is wrong by at least the length of
the outage, and the only correction available is a write we cannot verify. *(That last step is
inference from the hardware, not a measurement — but nothing should be built that depends on it
being wrong.)*

**What this costs, stated rather than buried.** A Home Assistant automation makes a BLE write at
each schedule boundary, and a write can fail; an on-device schedule cannot miss. In exchange we
get sun events, presence and template conditions, seasonal changes, one place to edit it all,
and a trace when it misbehaves — against a device offering a weekday mask, one window and one
work/pause pair per record. The resilience argument is weaker here than it first sounds anyway:
this setup already cannot survive a mains cut unattended, because a human has to press the base
button ([ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller)).

**A consequence that must be said out loud:** while this integration is installed, the phone
app's own schedules never fire. They are **masked by record 1, not erased** — clearing record 1
hands control straight back. That belongs in the README, and it is there.

**~~What would change this.~~ ✅ Settled the same day, in this decision's favour.** Reason 1
rested on a result we did not have: whether arbitration is **record-id priority** or
**last-write-wins**. Phase B of the first 2026-08-09 experiment wrote record 1 *second*, so both
models predicted what it saw. A second session that evening wrote record 1 **first** and record
2 second, and the device stayed silent — **record-id priority**
([`protocol.md`](protocol.md#-it-is-record-id-priority-not-last-write-wins-2026-08-09-second-session)).

An always-matching record 1 therefore does hold the line against an app write to any other
record, and reason 1 stands as written. Had it come back last-write-wins, reason 1 would have
collapsed and this decision would have needed re-deriving from reasons 2 and 3 alone — which on
their own are not why the records were rejected. The gate was worth keeping: the answer was not
knowable from the first experiment, and the experiment that settled it was designed *because*
this paragraph said what was missing.

---

## ADR-010 — The proxy is a Seeed XIAO ESP32C6

**Status:** accepted · **Date:** 2026-08-09 · **Supersedes [ADR-003](#adr-003--use-the-dfrobot-firebeetle-2-esp32-e-already-owned)**

**Context.** [ADR-003](#adr-003--use-the-dfrobot-firebeetle-2-esp32-e-already-owned) chose the
FireBeetle because it was owned and sufficient, and set its own exit condition: revisit size
once the system works. #5 measured both links, the system worked, and a C6 was ordered.

**Decision.** The XIAO ESP32C6 is the proxy. The FireBeetle is retired to spare and **kept**,
not disposed of.

**This remains a downsizing decision, not a fix.** Nothing about the FireBeetle failed. It is
smaller and tidier on a desk that also holds the diffuser. Stated again here because the swap
*did* surface a defect, and a later reader could easily misread that as the reason for the
change.

**What the swap cost, measured rather than assumed** — same position, RF switch driven:

| | FireBeetle | C6 |
|---|---|---|
| RSSI to the Kirri | −56 dBm | **−68 dBm** |
| WiFi | −26 dBm | **−36 dBm** |

A ~10–12 dB penalty on both links. Both stay inside the workable band, but BLE margin drops
from ~30 dB to ~20 dB. That is the real price, and it is why the FireBeetle is kept: it is the
fastest way to distinguish a C6 problem from a site problem.

**Swapping back is not a two-line revert**, despite the board itself being two lines. The
XIAO-only `on_boot` and `output:` blocks that bias the RF switch must come out too — GPIO3 and
GPIO14 are ordinary pins on the FireBeetle, and driving them there would be meaningless at
best. The rollback is: two board lines, plus delete both RF-switch blocks. Written out because
a half-done revert would leave a config that looks right and drives the wrong pins.

**Do not read that 10–12 dB as "the chip antenna" yet.** Seeed's wiki and Seeed's own forum
disagree on whether the built-in antenna wants GPIO14 driven LOW (what we do) or left in INPUT
mode, and only the first has been tested here. If INPUT is better, some of this penalty is
residual switch loss rather than antenna. Tracked in
[`hardware.md`](hardware.md#open-is-gpio14-driven-low-actually-the-best-built-in-antenna-setting).

**The defect this exposed, which is the part worth remembering.** The C6 routes both antennas
through an RF switch that is only biased when GPIO3 is driven LOW. Undriven, the radio works
at roughly **20 dB down** — a quietly bad board, not a dead one, which is far harder to notice.
`hardware.md` had explicitly advised *against* driving that GPIO on the grounds that the
onboard antenna needed no configuration. Both the advice and the ~6 dB penalty estimate it
rested on are now corrected there; the A/B that corrected them is in
[`lab/soak-2026-08.md`](lab/soak-2026-08.md).

**Why it was found at all:** WiFi and BLE degraded by *different* amounts, and one shared
antenna cannot do that. The asymmetry forced the search for two stacked causes instead of one.
A single averaged "signal is worse" number would have been accepted as the chip antenna and
the 20 dB would still be on the floor.

**What would change this.** Sustained GATT connect failures or write timeouts at −68 dBm. The
response is not to buy a board — it is to fit a u.FL antenna (the pins are already driven;
GPIO14 HIGH selects it) or drop the FireBeetle back in, and see which moves the number.

---

## ADR-011 — "Off" is an always-matching record 1 with `work = 0`

**Status:** accepted · **Date:** 2026-08-09 · **Implements [ADR-009](#adr-009--scheduling-lives-in-home-assistant-not-in-the-devices-schedule-records) reason 1 · Fixes #27**

**Context.** Turning the diffuser off in Home Assistant did not turn it off. The UI reported
`off`, the write was echo-confirmed, and the atomiser kept running. The integration modelled
**record 1 as if it were the whole device**: off cleared record 1, and a cleared record matches
nothing, so the device *fell through* to the phone app's schedules in records 2–4 and carried
on. Home Assistant then read record 1 back, saw zeros, and truthfully reported what record 1
said while being wrong about the diffuser.

The failure needs the phone app's schedule screen to have been saved at least once — which is
the default state of any unit whose owner has opened the app — and it is invisible in the *on*
direction, because "on" already wrote an all-day record that outranked everything. That is why
#13's testing passed.

**Decision.** Home Assistant **owns record 1 and keeps it always matching**, in both switch
positions. On and off are the same record — all seven days, `00:00`–`23:59` — differing in one
field:

| | Record 1 |
|---|---|
| **on** | enabled, all days, `00:00`–`23:59`, `work` = selected dispense time |
| **off** | enabled, all days, `00:00`–`23:59`, **`work = 0`** |

State is read the same way round: resolve the device by the arbitration rule rather than by
reading record 1's day mask. Record 1 is queried first and, **only if it does not match now**,
records 2–4 are read and the first matching record wins.

**Why.** A record that always matches always wins arbitration, so records 2–4 are unreachable
and there is nothing to fall through *to*. `work = 0` then makes that winning record dispense
nothing. Both halves are measured, not reasoned: on 2026-08-09 the frame was written **while
record 2 was actively dispensing** at 6 s / 10 s, echoed verbatim, read back verbatim, and the
mist stopped — three minutes after phase 4a had deliberately reproduced the bug by clearing
record 1 and watching record 2 take over
([`protocol.md`](protocol.md#-it-is-record-id-priority-not-last-write-wins-2026-08-09-second-session)).

Three properties make this cheap rather than merely correct:

- **the poll still costs one query.** A matching record 1 makes the rest of the table
  unreachable, so there is nothing worth a radio connection behind it. Records 2–4 are read
  only in the state where the device is genuinely arbitrating over them;
- **it is self-healing.** The app re-pushes its schedules into records 1–3 on every connect, so
  it *will* take record 1 back. The next on or off from Home Assistant reclaims it — no repair
  flow, no user action, and the coordinator logs the interim once;
- **`IntensityMemory` already refused to absorb a `work = 0` echo.** The machinery built in #13
  to stop the old off frame erasing the remembered intensity was already exactly the right
  shape for this.

**What this costs, stated rather than buried.** While the integration is installed, **the phone
app's own schedules never fire.** They are masked by record 1, not erased — clearing record 1
by hand hands control straight back. That is the same cost ADR-009 already accepted, arriving
in the README rather than as a surprise; schedules move to Home Assistant automations.

**Alternatives considered.**

- **Clear all four records on off.** Destroys the user's app configuration, and the app
  re-pushes it on its next connect anyway — so it is both rude and ineffective.
- **Cache records 2–4 in Home Assistant and restore them.** The cache is stale the moment the
  app writes, and there is no event to tell us. It would fail the same way #27 did, only later
  and with more machinery in the way.
- **`work = 1, pause = 65535`** instead of `work = 0` — the fallback if a zero-length burst had
  been rejected. It would have dispensed one second roughly every 18 hours: nearly off, not
  off. Unnecessary once `work = 0` was measured.

**What would change this.** Evidence that the `PW` byte participates in arbitration. Every
record ever read off this device has `PW = 0x01`, including empty ones, so the matcher ignores
it deliberately — of the two untested readings, honouring a flag the device ignores would
report *off* while the diffuser mists, which is #27 again. If `PW = 0x00` turns out to make a
record lose arbitration, an app-written disabled record could be reachable when we think it is
not, and the matcher has to consult it.

---

## ADR-012 — A stale reading beats `unavailable`, and the fix ships with its own meter

**Status:** accepted · **Date:** 2026-08-13 · **Rests on [ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller) · Fixes #38**

**Context.** The 72-hour #14 soak measured **13 `unavailable` periods, longest 903 s** — every
one of them a *single* failed poll, recovered by the next. The entity's availability was
`last_update_success and device_present`, and nothing clears `last_update_success` until a poll
succeeds, so one missed reading cost a full poll interval of unavailability. Two scheduled soak
exercises were skipped outright because the switch was unavailable when the harness went to
drive it, so the amplification was destroying measurement coverage as well as usability. At the
observed 4.5 % poll-failure rate, tolerating one miss is worth roughly a 20× reduction in
visible unavailability — **before #35, the underlying fault, is touched at all**.

**Decision.** Entities survive **one** failed poll and keep showing their previous reading; the
second consecutive failure takes them `unavailable`. `device_present` remains a hard gate,
unchanged. The coordinator still raises `UpdateFailed` and `last_update_success` still flips —
this changes what the *entities expose*, not what the coordinator records. And the change ships
with a diagnostic sensor counting every failed poll, tolerated or not.

**Why masking a failure is honest here, and not a lie.** The usual objection is that this hides
a dead device. It cannot, because **"is the device there" is already answered separately and
correctly** by the other term in that `and`: `device_present` reads Home Assistant's own
advertisement tracking and goes false ~5.5 minutes after the diffuser stops advertising. So
`last_update_success` never had to carry "is it there" as well — it can mean "is our reading
fresh", which is the question it actually answers.

What makes a stale reading cheap is [ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller)
specifically: **Home Assistant is the sole writer.** The device changes state only when we
change it, so a held reading is overwhelmingly Home Assistant's own last command read back.
`unavailable` breaks automations and blanks the UI; fifteen-minutes-old-but-correct does not.

Tolerance requires a previous reading to hold — with `data` still `None` the entity is honestly
unavailable, which is the startup path `__init__` already chose deliberately over
`ConfigEntryNotReady` (#8, #15).

**An absence is not a miss, and voids tolerance outright rather than costing one of its lives.**
A missed poll says the link dropped a message; a device off air says it was somewhere we could
not see it, and anything may have happened to it there. Only a fresh reading lifts that — not
advertisements returning, which tell us the diffuser is present and nothing about what it did
while it was gone. Charging an absence one tolerance life instead would leave any outage shorter
than two poll intervals inside the budget, so the entity would flick back to its pre-outage state
the instant advertisements resumed, seconds ahead of the refresh that could confirm it. Showing a
state we cannot support is the shape of [ADR-011](#adr-011--off-is-an-always-matching-record-1-with-work--0)'s
bug, and it is not worth those few seconds.

**Why the counter is part of the decision, not an extra.** Every read-failure number this
project has — the 13 dropouts, the 4.5 %, the confidence interval that reconciles reads with
writes in [`lab/soak-2026-08.md`](lab/soak-2026-08.md) — was **inferred from `unavailable` periods in the
recorder**, because a poll leaves no other trace. This change erases that inference: a
tolerated miss is, by construction, invisible. The box has **no persistent Home Assistant log**
(`/api/error_log` returns 404 on this install), so there is no fallback channel.

Shipping the fix without the meter would mean the verification soak for #35 reporting a clean
box because the failures had been hidden, not because they had stopped — measuring a fix
against an instrument the same commit broke. Hence `sensor.…_failed_polls`: recorder-visible,
always available even when every other entity is not, and read by `tools/soak/soak.py`
alongside the unavailable count so the pair cannot be misread.

**Alternatives considered.**

- **Shorten the poll interval instead.** Treats the symptom, costs radio on a device where the
  echo — not the poll — is the real state signal, and 60 s of unavailability for a transient
  miss is still 60 s of a broken automation.
- **Expose the tolerance as a config option.** The question a user would tune it for is "is the
  link flaky enough to need more slack", and that is answered by the counter, not by guessing.
  An option costs a strings entry, a translation, a reload path and a support surface for a
  number nobody has evidence to pick.
- **An attribute on the switch rather than a sensor.** Home Assistant strips attributes from an
  `unavailable` state, so the counter would vanish at exactly the moment worth recording.
- **Log lines instead of an entity.** There is no log on this box to grep. This is the same wall
  #14's log-grep acceptance criterion hit, and the reason it was closed unsatisfiable.

**What would change this.** A second writer. If the phone app is ever used alongside the
integration, or an on-device schedule can fire without Home Assistant asking, then a held
reading is no longer overwhelmingly our own last command and the trade weakens.
[ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller)
withdrawing the phone-app requirement is load-bearing for this entry; if it is ever revisited,
revisit this too. Evidence that failures **cluster** rather than arriving independently would
also change the sizing — the ~20× estimate assumes independence, and the verification soak is
what tests it.

---

## ADR-013 — Silence is retried, disagreement is not

**Status:** accepted · **Date:** 2026-08-13 · **Follows [ADR-012](#adr-012--a-stale-reading-beats-unavailable-and-the-fix-ships-with-its-own-meter) · Fixes #35**

**Context.** `_async_run()` has always retried a failed command once, and has always caught the
wrong thing. Its `except` tuple was `(BleakError, asyncio.TimeoutError)`, written for a
cached-but-dead connection — but a write whose `A5FB` echo never arrives raises
`KirriCommandFailed`, which is neither, so it propagated on the **first and only attempt**.

That is three of the #14 soak's 22 verified writes, plus a fourth caught the day after it
closed, all with the same signature: a connect, `ECHO_TIMEOUT_S` elapsing in silence, an error
at 6.7–7.7 s. The read side shows the same fault at an inferred 4.5% of polls, and the two
confidence intervals overlap — one defect in the shared path, not two
([`lab/soak-2026-08.md`](lab/soak-2026-08.md)). The read symptom is the worse one: a failed write returns an
error to whoever asked for it, a failed poll marked the entity `unavailable` for 900 s.

**Decision.** A missing echo gets its own exception, `KirriEchoTimeout(KirriCommandFailed)`,
and **that** goes in the retry tuple. An echo **mismatch** keeps raising the plain parent and
is not retried. Still exactly one retry. The fix ships with a counter, `sensor.…_echo_timeouts`.

**Why the two unconfirmed-write cases are not one case.** They differ in what the device did:

| | The device… | Resending would… |
|---|---|---|
| **No echo** (`KirriEchoTimeout`) | ignored us | plausibly work — measured 2 for 2 |
| **Echo mismatch** (`KirriCommandFailed`) | answered, with something else | likely reproduce the disagreement |

Widening the tuple to `KirriCommandFailed` would have caught both, which is why the fix needed
a new exception rather than a new entry. A mismatch has never been observed on air; if it ever
is, it is a protocol bug worth seeing rather than one worth papering over. Subclassing keeps
every `except KirriCommandFailed` written before this working unchanged.

**Why not simply raise `ECHO_TIMEOUT_S`.** Because the echo is **absent, not late**, and that
was measured rather than assumed. `tools/kirri_probe.py` waited 20 s instead of 5 s across 60
cycles: 58 echoes, median 0.02 s, **max 0.11 s**, none past 5 s. The current timeout already
sits 45× above the slowest echo ever recorded, so there is no tail to catch — raising it buys
nothing and makes every genuine failure slower, pushing the command path from ~15 s to ~21 s
and past the soak harness's own 45 s-per-leg allowance.

**Why retrying a write is safe.** Every frame this transport sends is **absolute, not
incremental**: a schedule record is set to a value, never adjusted by one. The awkward case —
the write did land and only its echo went astray — resends the same record with the same
contents, and the device sets it to what it already holds. There is no double-apply to fear,
which is what makes a mutating command retryable at all.

**Why the counter is part of the decision, not an extra.** Identical reasoning to
[ADR-012](#adr-012--a-stale-reading-beats-unavailable-and-the-fix-ships-with-its-own-meter),
one layer down, and it is the second time this project has hit it: **the fix erases the
evidence it should be judged on.** Before it, every missing echo was a visible failure — a
write error in the harness log, or an `unavailable` entity. After it, a rescued command is
indistinguishable from one that never had a problem, so an unchanged fault rate reads as a
fixed box. There is no Home Assistant log on this box to fall back on.

It also answers the question the re-soak cannot. n=22 gave a 2.9–34.9% interval; hourly
exercises would only reach n≈48, still too wide to call a fix proven. Counting retries and
their outcomes measures the mechanism **directly**, where an end-to-end success rate is an
inference needing hundreds of samples.

The retry counters are **scoped to echo-driven retries**, with any other cause counted apart as
`other_retries`. Not fussiness: `_async_run` retries a stale link too, and one shared pair
would let a stale-link retry report itself as a rescued echo — a window with no missing echoes
could read *"0 timeouts, 3 rescued by the retry"*, the instrument narrating a fault that did
not occur. `other_retries` gets no outcome breakdown, because that fault predates this work,
was never instrumented before it, and still surfaces on its own terms.

`echo_timeouts` is the sensor's *state* — and therefore the series in long-term statistics —
because it is the only one of the counters that does not describe our own retry policy.
Change the policy and `retries`, `recovered` and `failures` change meaning; `echo_timeouts`
keeps describing the device and the link. It lives on the transport rather than the
coordinator so that it spans **reads and writes alike**, which is the population
[`lab/soak-2026-08.md`](lab/soak-2026-08.md) could only reach through a derived poll
denominator — and it splits them, `on_reads` / `on_writes`, so the shared-defect claim that
page argues for can be
falsified by a later window rather than only re-asserted. The split is derived from the one
fact the transport already has (only a write knows the frame it requires back), not from a
flag a caller must remember to pass.

**Alternatives considered.**

- **Retry more than once.** Giving up and letting the coordinator try again next cycle is what
  implements "retry forever, report unavailable" (#8). A loop inside the transport would hold
  both the command lock and the proxy's single connection slot while it ran, which is the
  behaviour that tied the proxy up for ~4 minutes when a config flow met a non-Beacon device.
- **Retry only writes.** The read path shows the same fault at a higher absolute count and the
  worse symptom. A fix that skipped it would leave the larger population untouched.
- **Treat a missing echo as success.** The one thing this integration must never do — an
  ATT-layer accept proves nothing on this device (dossier 6.5), which is the rule the whole
  transport is built around.
- **Count in log lines instead of an entity.** Same wall #14's log-grep criterion hit:
  `/api/error_log` 404s on this box.

**What would change this.** Evidence that a resend can make things *worse* — a device that
double-applies an absolute record, or a mismatch that turns out to be transient and retryable
after all. Also a measurement showing echo timeouts cluster tightly in time: one retry 2–5 s
behind a failure assumes the fault is independent between attempts, and if silences arrive in
bursts the second attempt is inside the same burst and the retry buys much less than the
probe's 2-for-2 suggests. `sensor.…_echo_timeouts` is what will show that.

---

## ADR-014 — The public repo is cut fresh; this history stays private

**Status:** accepted · **Date:** 2026-08-17 · **Fixes #43 · Resolves the "make this repo public" pending row**

**Context.** Five places in the tree asserted that this repository was private and had to be
scrubbed before it could be published. #16 audited the identifiers, #42 removed them, and #48
publishes the integration for a HACS audience — which forced the question the pending row had
been holding open since the start: does *this* repository become the public one, or does a new
one get cut from it?

**Decision.** This repository stays **private, permanently**. `luguina/kirri-beacon-hass` (#48)
is a **fresh repository with no shared history**, populated by copying the scrubbed tree.

**What the history actually holds** — measured, not assumed:

| | |
|---|---|
| The diffuser's BLE MAC at `main`'s tip | gone (#42) |
| …in the trees of earlier commits | **63 of main's 68** — `git checkout` any pre-scrub commit and it is right there |
| Files that ever carried it | **2** (`docs/research/dossier.md`, `docs/captures/2026-08-08-nrf-connect-gatt-session.txt`) |
| Lines mentioning it across all of `git log -p main` | **12** |
| Commits authored as `luis@uguina.com` | **46 of 68** (the rest are `luguina@users.noreply.github.com`) |

**Why not `git filter-repo` and flip this repo public?** Not for the reason #43 assumed. That
issue argued the MAC was too widespread to rewrite — "129 times across 44 commits." The real
figures are above and they are *small*: `--replace-text` over 2 files and 12 lines, plus a
`--mailmap` for the author address, would take seconds and work. The volume argument does not
survive contact with the repository, so it is not the one this decision rests on.

Three that do:

- **A rewrite is irreversible surgery on the only copy** of 68 commits of protocol
  reverse-engineering, hardware iteration and soak evidence. A botched one loses the lab
  notebook, and the paragraph below argues there is nothing to win in exchange. `git init` in a
  fresh directory risks nothing at all.
- **Every SHA changes.** #44–#49 are queued against this tree, and every commit hash quoted in an
  issue thread or an ADR dangles afterwards.
- **It unpublishes nothing.** True in general, and honestly weak *here* — the only clone is mine,
  so there is no third party holding the originals. Listed because it is the argument usually
  made for rewrites, and it is not what carries this one.

**The reason that actually decides it: the history is not the product.** This repository is a
**lab notebook** — 68 commits of decoding an undocumented BLE protocol, two proxy boards
(ADR-003, ADR-010), a withdrawn requirement (ADR-008), and soak runs whose value is that they
record what broke. What #48 publishes is an **integration**: `custom_components/`, install
instructions for HACS, brand assets, CI. Those are different trees for different readers. The
public repo needs a *curated* tree, not this tree minus some strings — so a rewrite would have to
be followed by the curation anyway, and the curation is the whole job. Copying files into a fresh
repo **is** the curation, done once instead of twice.

There is also material here that should not go public in any form. The dossier and the capture
logs are derived from a decompiled vendor APK, and #45's position is that vendor material stays
out of version control. Importing the history would carry that derivation — and the reasoning
that documents it — into a public repository, which is precisely the exposure #45 exists to
avoid.

**Why #42 still had to happen, given this.** The obvious objection to a permanently-private repo
is that scrubbing it was wasted work. It was not, for two reasons. The public tree is copied
**file by file from this one**, so a scrubbed source means the copy is clean *by construction*
rather than by a second review pass — and #16's lesson was exactly that a review pass which
structurally cannot see the leak keeps reporting clean. And it means privacy is no longer the
only thing standing between a LAN address and the internet; a repo-visibility misclick stops
being a disclosure. The standing invariant and the audit command live in
[`privacy.md`](privacy.md).

**What would change this.** Nothing short of the identifiers ceasing to be sensitive, and a BLE
MAC and a set of LAN addresses for a device in someone's house do not expire. If the lab notebook
were ever worth publishing on its own merits, it would go out as its own repository from its own
curated tree — by this same route, not by making this one public.

---

## Pending

| # | Decision | Blocked on |
|---|---|---|
| — | Whether to offer the Beacon protocol upstream to `scent-assistant` | A working integration of our own first |
| ~~—~~ | ~~Whether to shrink the proxy hardware~~ | **Resolved 2026-08-09** → [ADR-010](#adr-010--the-proxy-is-a-seeed-xiao-esp32c6) |
| ~~—~~ | ~~Whether to make this repo public~~ | **Resolved 2026-08-17** → [ADR-014](#adr-014--the-public-repo-is-cut-fresh-this-history-stays-private) |

**Resolved:** #9 → [ADR-007](#adr-007--build-our-own-integration-in-python-the-esp32-stays-a-stock-proxy) ·
#8 → [ADR-008](#adr-008--the-phone-app-requirement-is-withdrawn-ha-becomes-the-sole-controller) ·
#17's design question → [ADR-009](#adr-009--scheduling-lives-in-home-assistant-not-in-the-devices-schedule-records) ·
proxy hardware → [ADR-010](#adr-010--the-proxy-is-a-seeed-xiao-esp32c6) ·
#27 → [ADR-011](#adr-011--off-is-an-always-matching-record-1-with-work--0)
(the issue itself stays open for the cleanup that follows from it) ·
#38 → [ADR-012](#adr-012--a-stale-reading-beats-unavailable-and-the-fix-ships-with-its-own-meter) ·
#35 → [ADR-013](#adr-013--silence-is-retried-disagreement-is-not) ·
repo visibility → [ADR-014](#adr-014--the-public-repo-is-cut-fresh-this-history-stays-private)
