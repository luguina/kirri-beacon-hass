# Hardware

The proxy is a stock ESPHome **Bluetooth proxy**: any ESP32 with WiFi and BLE will do, because
the job is trivial — carry GATT traffic between Home Assistant and a diffuser a metre away. No
Kirri logic runs on it. The firmware is [`../esphome/kirri-proxy.yaml`](../esphome/kirri-proxy.yaml),
flashed as-is, with flashing and adoption notes in [`../esphome/README.md`](../esphome/README.md).

Two settings in that file are load-bearing and worth knowing about even if you never open it:
**`bluetooth_proxy: active: true`** (without it the proxy forwards advertisements only and no
GATT write ever reaches the diffuser — the failure is silent) and **`min_version: 2026.5.0`**
(below it, WiFi/BT coexistence causes intermittent `status=0x85` GATT-connect failures that
look exactly like a protocol bug).

**This page is the transferable half.** Every claim on it was measured on one board, at one
address, over one 72-hour window; that record — MACs, medians, timestamps, the boards this
project happened to own — is in [`lab/soak-2026-08.md`](lab/soak-2026-08.md). Read this page
for what to do, that one for the evidence that it's true.

## ⚠️ On a XIAO ESP32C6, the RF switch is mandatory

**Both** antennas on the XIAO C6 sit behind an RF switch that is only biased when **GPIO3 is
driven LOW**; **GPIO14** then selects onboard (LOW) or external u.FL (HIGH). Leave GPIO3
undriven and the radio still works — it is simply **~20 dB down**. That is the dangerous
failure mode: not a dead board, just a quietly bad one.

**Why it still works at all**, which is what makes this so easy to miss: an unpowered RF switch
is not an open circuit, it is roughly **30 dB of isolation**, so signal leaks across it. The
board therefore behaves like one with a bad antenna rather than one with no antenna — every
link still associates, every device is still discovered, and nothing anywhere reports an error.
Seeed's own forum thread has users measuring ~25 dB of degradation from this; we measured
~20 dB. Independent agreement on both the mechanism and the rough magnitude.

Measured at one spot, before and after driving the pins — the delta is the finding, the
absolute numbers belong to that spot:

| | Before | After |
|---|---|---|
| WiFi | −51 dBm | **−31 dBm** |

**The A/B is WiFi-only, deliberately noted rather than quietly left short.** BLE was not
sampled before and after, because WiFi answers the question on its own: both radios sit behind
the *same* switch, so 20 dB recovered on one is 20 dB recovered on both. If you repeat this on
your own board, WiFi is also the easier number to read — it is a single association, where BLE
RSSI needs a 90-second sample to mean anything.

This page used to say the RF switch was about the *external* antenna only, and that adding it
would be "complexity to solve a problem the measurements say you don't have." That was wrong,
and wrong in the expensive direction — the advice would have left 20 dB on the floor while
every number still looked plausible. The pins are driven in
[`../esphome/kirri-proxy.yaml`](../esphome/kirri-proxy.yaml) under `on_boot`, at priority 800
so the switch is biased before either radio starts. **Do not remove them when the board is a
C6, and do not add them when it is not** — GPIO3/GPIO14 are ordinary pins on other boards, so
a half-done board swap leaves a config that looks right and drives the wrong pins.

**How it was caught, since the same reasoning generalises.** The clue was not the table above
but the comparison against the board this one replaced
([the A/B is in the lab notebook](lab/soak-2026-08.md#same-site-c6-instead-of-firebeetle-2026-08-09)):
WiFi and BLE had both dropped, but by *different* amounts. One shared antenna cannot be 5 dB
worse for one link and 22 dB worse for another, so the discrepancy had to be two separate
causes stacked — and separating them is what
turned "the chip antenna is bad" into a fixable defect plus a real but tolerable penalty. A
single summary number would have hidden it. Generalised: **a symmetric loss across both links
points at the radio; an asymmetric one localises the cause to whatever changed on the short
link — usually proximity.** That is how a board sitting a metre further from the diffuser than
its predecessor got caught as an 11 dB error without anyone measuring a distance.

Two caveats on the C6 itself: it is **single-core RISC-V**, so it carries the coexistence
caveat noted against the C3 below, and the proxy image leaves only ~240 KB spare in its app
partition — an ESPHome release that grows the BLE stack could overflow it, and the failure
would appear at build time on a board already in service.

### Open: is GPIO14 driven LOW actually the best built-in-antenna setting?

Seeed's wiki says GPIO14 **LOW** selects the built-in antenna. Seeed's own forum thread says
built-in wants GPIO14 left in **INPUT / high-impedance** mode, and that driving it LOW while
the switch is unpowered is the specific case that costs ~25 dB. We drive it LOW, and that
demonstrably works — it recovered 20 dB on WiFi — so this is not a live fault. But the two
sources disagree, and we have only tested one of them.

If INPUT mode recovers further signal, then part of the C6's measured penalty against a
larger board is still switch loss being misattributed to the antenna. **Cheap to settle:**
change `rf_antenna_select` to an input (or simply remove it and leave the pin undriven), OTA,
and re-run a 90-second RSSI sample from the same position. Roughly ten minutes, and it either
confirms the number or improves the link.

## Choosing a board

### The thing to understand first

This device has **two radio links with completely different requirements**, and conflating
them produces bad board decisions:

| Link | Distance | Antenna quality matters? |
|---|---|---|
| **BLE** — proxy → diffuser | ~1 m, same room, by design | Barely. A few dB on a 1 m link is noise |
| **WiFi** — proxy → access point | Whatever the house imposes | **Yes.** This is the long link |

So "the antenna is weak" is only ever an argument about the *WiFi* side, and whether it bites
depends on the measured WiFi RSSI at your install site. An earlier version of this file
rejected a small board on the grounds that *"range is the entire job of this device"* — wrong,
and wrong in an instructive way: it treated one number as decisive without checking which of
the two links it applied to. **Measure the link before theorising about it.** One 90-second
measurement settles what a page of analysis cannot.

### Rejected, and why the objection is worth its strength

| Board / product | Why not | Strength of the objection |
|---|---|---|
| **Shelly Gen2+** | **Cannot proxy active GATT connections** — advertisements only. The Active/Passive toggle in the Shelly integration refers to active *scanning*, not connections; HA's docs call this out as a known source of confusion. GATT writes are our entire requirement. | **Decisive.** Disqualifying on capability |
| **ESP32-H2** | No WiFi radio at all (802.15.4 + BLE). Cannot bridge to HA. | **Decisive.** Non-starter |
| **Seeed XIAO ESP32-C3 + W5500 PoE** | Listed on ESPHome's projects page, but HA's Bluetooth maintainer reports GATT connection failures on roughly 19 of 20 attempts across 20+ units. Also solves a problem most installs don't have — Ethernet. | **Strong.** Note this is the *W5500 PoE* board, not the plain XIAO ESP32C3 |
| **ESP32-C3 SuperMini** | Ceramic chip antenna laid out against the ground plane, ignoring the datasheet's stripline and keepout requirements; measured ~6 dB average penalty, 10+ dB through obstacles. Single-core, so WiFi/BT coexistence has less headroom under sustained proxy load. | **Weak-to-moderate, and conditional.** The antenna penalty only hits the WiFi link, so a comfortable WiFi RSSI at the install site neutralises it entirely |

The top two verdicts are facts about the products; the bottom two are conditional, and the
condition is a number you can measure in a minute.

### If the WiFi link turns out to be the constraint

In rough order of least disruption:

| Upgrade | Cost | Why |
|---|---|---|
| **FireBeetle 2 ESP32-UE** (DFR1140) | ~A$13 | Classic dual-core ESP32 with an external antenna connector. **The ESPHome config is unchanged** — a drop-in fix for a marginal WiFi link |
| **Seeed XIAO ESP32C3** | ~A$5 | Tiny, and ships with an external antenna in the box, which more than offsets the single-core chip. This is the *plain* XIAO — not the W5500 PoE variant rejected above |
| **Olimex ESP32-POE-ISO-EA** | ~€27 | ESPHome's and HA's own recommendation. Ethernet removes WiFi/BT coexistence as a variable entirely — the heaviest hammer, for when the link is the problem and you're done guessing |

## Siting, and reading the numbers

The proxy goes **next to the diffuser**, not somewhere convenient in between — BLE needs the
short hop, and WiFi travels through the house far better than BLE does.

**Site it clear of the mist plume, and read that as clearance rather than distance.** Plume
geometry, not separation, decides whether condensation and mineral residue reach the
electronics: a board 30 cm away but outside the plume path is safer than one a metre away
directly downwind. The two failure modes this guards against are slow and arrive disguised —
condensation causing leakage currents and then corrosion, and hygroscopic mineral film from
tap water — and both surface months later looking like flaky BLE. If the diffuser is ever
repositioned, re-check the plume path, not the distance.

| RSSI | Meaning |
|---|---|
| −60 dBm or better | Comfortable |
| −60 to −80 dBm | Workable, expect occasional retries |
| worse than −80 dBm | Move it — this will produce intermittent failures that look like protocol bugs |

Apply this to **both** links, and sample rather than spot-read: BLE RSSI swings several dB
packet to packet, so a single reading tells you almost nothing. A ~90-second sample is enough.
Do not assume WiFi will be the weaker of the two — it was predicted to be and
[was not](lab/soak-2026-08.md#final-install-site-2026-08-08-5), and the guess cost more
analysis than the measurement would have.

**Also measure the thing that matters, not just the easy thing.** RSSI predicts connection
success only loosely. The real pass condition is a GATT connection that establishes, negotiates
its MTU and tears down without leaking a proxy slot:

```
[conn] connected=True mtu=251 error=0
vendor service / write char / notify char   all FOUND
proxy connection slots: 3 free of 3         no leak on teardown
```

Advertisements arriving at a healthy rate is necessary but not sufficient — it is entirely
possible to hear a device perfectly and still fail to connect to it. One more runtime check
worth repeating after any config change: `bluetooth_proxy_feature_flags` should read **255**,
the proof that `active: true` took effect. A passive proxy fails silently — HA sees the device
and cannot write to it, with no error explaining why.

An unreliable radio link is the most expensive failure mode here, because it is
indistinguishable from a wrong protocol until you check. Record the numbers when you install
the board, so a later "it stopped working" has a baseline to compare against.

## Power-cycle behaviour

The Beacon is **mains-powered with no battery**, so it *will* lose power eventually. Issue #8
tested whether it recovers afterwards without someone walking up three floors.

> ### ⚠️ It does not.
>
> **The Beacon powers up in the OFF state after mains is restored, and stays there until
> somebody presses the physical on/off button on its base.** It is a momentary soft-power
> button, not a latching mains switch — restoring power is not enough to start the device.
>
> **No software can fix this, and a smart plug does not help.** Switching the mains back on
> just leaves the unit sitting there, off, not advertising. This setup is unattended in every
> respect *except* recovery from a power cut.

| | |
|---|---|
| Recovers unattended after a power-cycle? | **No** — needs a human to press the base button |
| Does the BLE address survive a power cycle? | **Yes** — same MAC, reconnected without re-discovery |
| Do settings survive a power cycle? | **Yes** — the schedule record came back intact (`work=6s pause=120s`, all days, 00:00–23:59) from non-volatile storage |
| Does BLE need the 5-second button hold after a power-cycle? | **No** — once powered on by hand, GATT connected in 0.85 s untouched |

**Read the scope of this precisely, because it is narrower than it first sounds.** The
limitation applies to **mains interruption and nothing else**. Software power control is
completely unaffected: turning the diffuser off over BLE and back on over BLE both work with
nobody touching the device, each confirmed by its `A5FB` echo. A BLE "off" is not a state that
needs a physical button to leave; a *power cut* is.

**Consequences for the build:**

- **The integration must treat "not advertising" as a normal state, not a fault.**
  Unavailable-until-a-human-presses-it is an expected mode with a human-scale duration. The
  reconnect policy is therefore "retry indefinitely at a modest interval and report
  unavailable", *not* a bounded retry that gives up and needs a reload to recover.
- **An unreachable alert is worth building.** Since the only remedy is a person walking to the
  device, HA knowing before you do is the entire mitigation — an automation that notifies after
  ~15 minutes unreachable turns a silent failure into a short errand.
- **A UPS is the only true fix**, if it ever becomes annoying enough. Prevent the power loss,
  because you cannot automate the recovery.

## Connection behaviour

One complete command cycle through the proxy — connect, subscribe, write, read the `A5FB`
echo, unsubscribe, disconnect:

| | |
|---|---|
| GATT connect latency | **0.02 – 4.12 s**, median 0.86 s (n=58) |
| Radio held per command | **~230 ms** |
| Full cycle incl. proxy API setup | 3.1 – 5.1 s |
| Back-off needed between commands | **None** — 6/6 back-to-back cycles, no cool-down (n=6) |

**The connect-latency spread is the advertising interval, not instability.** The Beacon
advertises at ~1.10/s, so a connection cannot open until the next advertising window happens to
come round; the median tracks that rate and the tail is several missed windows in a row. Do not
read a 2-second connect as a degraded link.

> **That range was first recorded as 0.75–2.71 s from six consecutive successes, and the error
> is the lesson.** Six samples at a desk covered the middle of the distribution and none of its
> tail, and the tail is the only part that ever causes a failure. Anywhere you reason about
> "connect plus the echo timeout", the band runs to **~9.1 s**, not the ~7.5 s a 2.71 s ceiling
> implies.

**There is no idle-disconnect window to tune** — the protocol exchange itself sets the floor
above. Whether to hold the link open afterwards is a design parameter to *choose*, and this
build chooses to: the integration keeps it open for `drain_seconds` after each command
(default 2.0 s, configurable 0–30 s), and `_async_connect()` reuses a live client, so a
follow-up command inside that window pays nothing. A cold write costs **~1.9 s**, the same
write on a warm link **~0.09 s** — a 20× difference.

That changes what a measurement *means*, which is the part worth carrying elsewhere: **any
timing taken less than `drain_seconds` after a previous command is a warm-link number** and
says nothing about the connect path — which is where GATT error 133, proxy slot exhaustion and
advertising-window waits all live. Anything measuring this stack must space its samples wider
than the drain window or it will never touch the code that actually fails.

## How to read a reboot: the die-temperature test

A proxy that reboots unattended raises one question — was that the firmware or the power? — and
the ESP32's on-die temperature sensor answers it for free.

**A watchdog reset, a panic or a `restart` keeps the silicon hot**, so the sensor comes back at
whatever it was running at. **Losing power lets it cool.** Against a running plateau of ~58 °C,
a return at ~42 °C is a brief interruption of seconds; a return at ambient (~30 °C) is a board
that was fully unpowered for minutes.

So a reboot that returns hot is the firmware's fault and wants `esphome logs` or the `debug:`
component's reset-reason sensor; one that returns cold is the power path, and no amount of
firmware instrumentation will explain it. Corroborate with anything physical that moved at the
same moment — a persistent RSSI shift across the boot, say, since silicon does not gain 10 dB
by rebooting.

**And do not count sub-second `status` drops as reboots at all.** If uptime runs straight
through them, they are API reconnects to a node that never went away. Counting them as device
instability can triple an apparent fault rate.
