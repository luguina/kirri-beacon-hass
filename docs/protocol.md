# Kirri Beacon — BLE protocol

**Status: ✅ RECOVERED FROM VENDOR SOURCE AND CONFIRMED ON DEVICE.** Not inferred, not sniffed
— read directly out of the Kirri app's own TypeScript, recovered from source maps shipped
inside the APK, then **validated by writing the power-on frame from nRF Connect and watching
the diffuser start** (2026-08-08).

This supersedes the earlier Aroma-Link guess entirely. That hypothesis was **wrong**: the
Beacon is not an Aroma-Link device and shares neither its UUIDs nor its frame format.

Source: `com.scentaustralia.kirri` → `assets/public/assets/index-DR_dSHQ_.js.map` →
`src/constants/BleConfig.ts`, `src/utils/commandBuilder.ts`,
`src/services/BeaconDeviceHandler.ts`, `src/services/BluetoothService.ts`.

> The app is a **Capacitor/Ionic** hybrid using `@capacitor-community/bluetooth-le`, and the
> build shipped **unminified source maps**. The complete original source is therefore
> recoverable — 57 app files, including every protocol constant.

---

## Exactly what this was observed on

Recorded because "a Kirri" is not specific enough to act on: the range spans at least two
incompatible protocols, and anyone matching this spec against their own unit needs to know
whether they have the same thing.

| | |
|---|---|
| Product | **Kirri Beacon**, SKU **SAH5910**, Scent Australia Home |
| Device name | `AJBLE100` — in the **scan response** and in GATT `0x2A00`. **Not in the advertisement**: not once in 20 raw packets through the proxy ([dossier §6.1 amendment](research/dossier.md#61-advertisement--observed-2026-08-08-nrf-connect-for-android)). nRF Connect merges the scan response into the advertisement and so makes it look like it is on air. Nothing keyed on `local_name` can match this device on the path Home Assistant actually uses |
| Advertised service UUID | `0000FFF0` — **which the device does not serve.** The only thing genuinely on air, and therefore the only thing discovery can match on |
| Firmware version | **Not exposed.** There is no Device Information service (`0x180A`) anywhere on the device, so there is no firmware, model or serial string to record — and no way to tell two firmware revisions apart |
| Phone app | `com.scentaustralia.kirri` |
| Observed | 2026-08-08 (protocol recovery, first confirmed write) and 2026-08-09 (record arbitration, on-device) |

**The missing firmware string is a real limitation of this document, not an omission.**
Everything below was true of one physical unit on those dates. If Kirri ships a firmware
change there is no version anyone can compare against, so the only way to detect it is
behaviour — which is why `tools/frames.py` checks the codec against echoes captured from the
real device rather than against a table someone typed in.

---

## The app supports two device families

| Model | Service | Write | Notify | Frame style |
|---|---|---|---|---|
| **Element** | `0000fff0-…` | `0000fff2-…` | `0000fff1-…` | `A5 AA AC <xor> …  C5 CC CA` — Aroma-Link-shaped |
| **Beacon** ⭐ *ours* | `EDFEC62E-9910-0BAC-5241-D8BDA6932A2F` | `0783B03E-8535-B5A0-7140-A304D2495CBA` | `0783B03E-8535-B5A0-7140-A304D2495CB8` | `A5 <cmd> … <sum>` |

The Element protocol *is* essentially Aroma-Link, which is why the original research pointed
there. **The Beacon is a completely different device.** Our unit advertising `0xFFF0` while
serving neither `FFF0` nor anything like it is most plausibly a leftover advertising config
copied from the Element product — it is actively misleading and cost us a wrong hypothesis.

---

## Transport (Beacon)

| | |
|---|---|
| Service | `EDFEC62E-9910-0BAC-5241-D8BDA6932A2F` |
| Write | `0783B03E-8535-B5A0-7140-A304D2495CBA` (`WRITE`, `WRITE NO RESPONSE`) |
| Notify | `0783B03E-8535-B5A0-7140-A304D2495CB8` (`NOTIFY`) |
| Write type | **Write *with* response** — the app calls `BleClient.write()`, not `writeWithoutResponse()` |
| MTU | The **app** never negotiates one. Over an ESPHome proxy, ESP-IDF negotiates **251** on its own. Either way it doesn't matter — the longest frame is 14 bytes, so nothing ever chunks |
| Bonding | **Not used.** No pairing, no encryption anywhere in the app |

### GATT layout — ✅ enumerated through the ESPHome proxy, 2026-08-08

Handles are stable enough to be worth recording, but **resolve by UUID, not by handle** —
handles are only guaranteed constant between service-changed events.

| Handle | UUID | Properties |
|---|---|---|
| 1 | `0x1800` GAP | — |
| 3 / 5 / 7 | `0x2A00` name · `0x2A01` appearance · `0x2A04` preferred conn params | read |
| **8** | **`EDFEC62E-9910-0BAC-5241-D8BDA6932A2F`** — vendor service | — |
| **10** | `…CB8` notify | `notify` |
| 11 / 12 | `0x2902` CCCD · `0x2901` user description | — |
| **14** | `…CBA` write | `write`, `write-no-response` |
| 15 / 16 | `0x2902` · `0x2901` | — |

Note the **write** characteristic also carries a CCCD at handle 15, which is unusual for a
characteristic that cannot notify. Almost certainly a copy-paste in the vendor's GATT table
rather than anything meaningful — but it is another reason not to infer behaviour from the
descriptor layout on this device.

There is **no Device Information service**, so no firmware/model strings to key an
integration off.

---

## Frame format

```
┌──────┬───────┬─────────────┬──────────┐
│  A5  │  cmd  │   data…     │ checksum │
└──────┴───────┴─────────────┴──────────┘
   0      1        2..n-1         n

checksum = (sum of ALL preceding bytes, including the A5 header) & 0xFF
```

Simple sum-mod-256 over the whole frame. Note this differs from Element/Aroma-Link, which
XORs and covers the payload only — one more reason the old frames did nothing.

The same checksum applies to **inbound notifications**; the app validates them with it
(`BeaconDeviceHandler.validateNotification`).

### Commands

| Byte | Direction | Meaning |
|---|---|---|
| `0xFA` | → device | **Write schedule record** — also how power and intensity are set |
| `0xFC` | → device | Query status |
| `0xFD` | → device | Clock sync |
| `0xFB` | ← device | Schedule/config echo (notification) |
| `0xFE` | **← device** | Defined in the vendor source as "BT operations" and **never sent by the app** — but the device emits `A5FE01000000A4` unprompted on power-up (#13). Meaning unknown. |
| `0x06` | *(defined, never sent)* | `SET_SCHEDULE` — vestigial |

**There is no separate power command.** Power, intensity and scheduling are all the same
`0xFA` schedule-record write. That's the central insight of this protocol.

---

## `0xFA` — schedule record (the only command that matters)

```
A5 FA │ PW DY RC SH SM EH EM WH WL PH PL │ CK
       └────────── 11 data bytes ─────────┘
```

| Off | Field | Meaning |
|---|---|---|
| 0 | `PW` | Power / record enabled — `0x01` on, `0x00` off |
| 1 | `DY` | Weekday bitmask (below) |
| 2 | `RC` | **Record ID**, `0x01`–`0x04` |
| 3 | `SH` | Start hour (0–23) |
| 4 | `SM` | Start minute (0–59) |
| 5 | `EH` | End hour |
| 6 | `EM` | End minute |
| 7–8 | `WH WL` | Work/dispense seconds, **big-endian uint16** |
| 9–10 | `PH PL` | Pause seconds, **big-endian uint16** |

Frame length is always **14 bytes**.

### Weekday bitmask

| Mon | Tue | Wed | Thu | Fri | Sat | Sun | All |
|---|---|---|---|---|---|---|---|
| `0x01` | `0x02` | `0x04` | `0x08` | `0x10` | `0x20` | `0x40` | `0x7F` |

### ⚠️ Record `0x00` — do not touch

The vendor's own source carries this warning:

> `// CRITICAL: Skip Record 00! It may act as a "master power switch"`
> `// Clearing Record 00 (Power=0x00) might disable the entire device`

The app only ever writes records **1–4**. We should do the same. Their devs evidently found
this the hard way; there is no reason for us to repeat the experiment on a A$350 device.

**This also corrects an earlier finding.** Kirri's marketing says *"schedule up to 5
intervals"*, which I previously flagged as independent corroboration of Aroma-Link's 5-slot
structure. It isn't — the Beacon has **4 user records** (plus the untouchable record 0). That
apparent confirmation was a coincidence, and it helped prop up a hypothesis that turned out to
be wrong.

### How power works

Power on/off is a schedule write to record 1 covering the whole day. **Both directions are the
same record, and they differ in one field — the dispense time:**

| | `PW` | `DY` | `RC` | Time window | Work/Pause |
|---|---|---|---|---|---|
| **ON** | `01` | `7F` (all days) | `01` | `00:00`–`23:59` | selected intensity |
| **OFF** | `01` | `7F` (all days) | `01` | `00:00`–`23:59` | **`0`** / unchanged |

Note the quirk: `createPowerCommand` **hardcodes `PW = 0x01`** even for off. Only
`createBeaconScheduleCommand` uses `PW` as a true enable flag.

### ⚠️ The vendor's own OFF frame does not switch the device off

**This is where the app and this integration part company**, and it is the single most
important correction in this document.

The vendor's `createPowerCommand` expresses "off" by clearing the day mask and zeroing the
times — `A5FA0100010000000000000000A1`, sent twice, the second as a post-command. That frame
was what this integration sent too, until #27.

A record matching **no days matches nothing**, and a record that does not match is *skipped*:
the device **falls through** to records 2–4 (see **Record arbitration** below). The phone app
populates records 1–3 by default, so on any unit whose owner has opened the schedule screen,
that OFF frame is echoed back perfectly, a readback of record 1 truthfully reports zeros, and
the diffuser keeps misting from record 2. **Clearing a record hands the device on. It does not
stop it.**

Since `createPowerCommand` writes record 1 and nothing else, and arbitration is device-side
behaviour independent of who wrote the record, **the vendor's app has exactly the same
hole** — a deduction from two documented facts rather than an observation, but it means this
is the design, not our misreading of it.

**The fix is to hold record 1 rather than clear it.** Keep the all-day window, which matches at
every instant and so always wins arbitration, and set `work = 0`:

```
OFF   A5 FA 01 7F 01 00 00 17 3B 00 00 00 78 EA
```

Verified on the device on 2026-08-09 (#27, phase 4b) under the hardest available conditions —
written *while record 2 was actively dispensing* at 6 s / 10 s. The device echoed it verbatim,
a `0xFC` readback two minutes later returned it verbatim (`work=0s pause=120s days=0x7F
00:00-23:59`), and the mist stopped and stayed stopped. A matching record with `work = 0` wins
arbitration and then dispenses nothing, which is what stops the search reaching records 2–4.

The cost is stated in the README rather than hidden: **while an always-matching record 1 is on
the device, the phone app's schedules can never fire.** They are masked, not erased — clearing
record 1 hands control straight back ([ADR-011](decisions.md#adr-011--off-is-an-always-matching-record-1-with-work--0)).

The retired frame is kept in `protocol.py` as `RETIRED_OFF`, because a capture containing it —
including one taken from the vendor's app — should still be recognisable for what it is.

### How intensity works

Intensity **is** the work/pause ratio — exactly as Kirri's documentation says. There is no
intensity register. Pause is always 120 s; only dispense time varies:

| Preset | Dispense | Pause |
|---|---|---|
| Delicate | 6 s | 120 s |
| Subtle | 12 s | 120 s |
| Radiant | 18 s | 120 s |
| Intense | 24 s | 120 s |

Custom values are supported by the app (it suggests 3–48 s dispense), so the field is a real
uint16 and not a preset index.

---

## Record arbitration — which record actually runs

**✅ Established by controlled experiment on-device, 2026-08-09 (#27).** Every write was
echo-confirmed and the device was restored to empty afterwards.

> **The device runs the *first enabled record, by record id*, whose day mask *and* time window
> both match the current time. A record that does not match is skipped and the device falls
> through to the next one. It does *not* dispense on the union of matching records.**

| Phase | Record 1 | Record 2 | Observed at the diffuser |
|---|---|---|---|
| A *(control)* | empty | all days, `00:00`–`23:59`, 6 s / 10 s | bursts every ~16 s |
| B | **all days, `00:00`–`23:59`, 3 s / 3600 s** | *(unchanged)* | **silent** — record 1 won |
| C | empty | empty | silent |

Phase B deliberately used `work = 3, pause = 3600` rather than `work = 0`. A zero-length burst
might be rejected as an invalid record, which would look exactly like "record 1 lost" and give
a false union reading.

### ✅ It is record-*id* priority, not last-write-wins (2026-08-09, second session)

Phase B above wrote record 1 *after* record 2, so both models predicted the silence it saw —
the doc stated the id-priority reading because it was the simpler one, not because it had been
isolated. A second session separated them, and added the `work = 0` measurement the fix
depended on. Every write echoed verbatim; every silence is bracketed by a positive control that
produced bursts, because a dead radio, a refused connect and an empty diffuser all look exactly
like silence.

| Phase | Record 1 | Record 2 | Expected if… | Observed |
|---|---|---|---|---|
| 1 *(control)* | empty | all days, 6 s / 10 s | — | **bursts** ✅ rig is live |
| 2 | empty | empty | — | silent |
| 3 | all days, 3 s / 3600 s, **written first** | all days, 6 s / 10 s, **written second** | id-priority → silent · last-write-wins → bursts | **silent** |
| 4a *(control)* | *cleared* | *(unchanged)* | — | **bursts** ✅ record 2 still live |
| 4b | **all days, `work = 0` / 120 s** | *(unchanged, bursting)* | `work=0` accepted → silent · rejected → bursts | **silent** |

**Phase 3 settles arbitration: record 1 won even though record 2 was written afterwards.** An
always-matching record 1 therefore holds the line against anything the app writes to records
2–4, which is what [ADR-009](decisions.md#adr-009--scheduling-lives-in-home-assistant-not-in-the-devices-schedule-records)'s
reason 1 was waiting on.

**Phase 4b settles `work = 0`: accepted, stored verbatim, and silent.** Note what phase 4a
proves on the way past — clearing record 1 handed the device straight to record 2 and the
bursts resumed. That is #27 reproduced deliberately, three minutes before the fix was measured
on the same device.

### The consequence, and it is not academic

`days = 0x00` matches nothing. So **clearing a record does not switch the device off — it hands
control to the next populated record.** With the phone app's schedules sitting in records 2–3,
Home Assistant zeroed record 1 and the diffuser carried on running from record 2, while the
readback of record 1 truthfully reported zeros. That is #27, and it is why the OFF frame above
holds record 1 instead of clearing it.

The same rule has a second consequence, and it is the one that bites in the *read* direction:
**a record's day mask alone does not tell you whether the device is on.** At 13:52 on
2026-08-09, record 1 still carried `days = 0x7F` from the app's Mornings entry — Home Assistant
reported on, while the window had closed at 12:00 and nothing was dispensing. Answering "is it
on" needs the time window, and, when record 1 does not match, the records behind it.

### What the phone app puts in these records

The app's "Adjust schedule" screen ships **three schedules, enabled by default, all seven
days**, and **pushes them to the device whenever it connects** — not only when you press save.
Read off the device on 2026-08-09 before anything had been changed by hand:

| Rec | Reply frame | Days | Window | Timing | App entry |
|---|---|---|---|---|---|
| 1 | `A5FB017F010A000C0000060078B5` | all (`0x7F`) | 10:00–12:00 | 6 s / 120 s | Mornings |
| 2 | `A5FB017F020C00120000060078BE` | all (`0x7F`) | 12:00–18:00 | 6 s / 120 s | Afternoons |
| 3 | `A5FB017F031200160000060078C9` | all (`0x7F`) | 18:00–22:00 | 6 s / 120 s | Evenings |
| 4 | `A5FB0100040000000000000000A5` | none | — | 0 / 0 | *(empty)* |

**Records 1–3 are not free space.** They belong to the app by default on any unit whose owner
has opened the schedule screen, and the app re-pushes them on its next connect — so an
integration that allocates them will be quietly overwritten rather than cleanly conflicted.

---

## `0xFC` — status query

```
A5 FC <rc> 00 00 00 <ck>
```

**✅ It queries any record, not just record 1 (2026-08-09, #27).** The vendor's app only ever
sends `A5FC01000000A2`, which left it open whether the record byte was honoured at all. It is —
and the reply carries the record id that was asked for:

| Query | Reply | |
|---|---|---|
| `A5FC01000000A2` | `A5FB017F010A000C0000060078B5` | record 1, 10:00–12:00 |
| `A5FC04000000A5` | `A5FB0100040000000000000000A5` | record 4, **empty** |

**An empty record answers with a correctly-labelled zeroed frame** — note the `04` still in
the reply — so "empty" is *readable*, not inferred from silence. Reading the device's entire
schedule table therefore costs four queries and no guesswork, which is what makes the
arbitration rule above something an integration can actually reason about at runtime.

---

## Connection sequence

From `BluetoothService.ts:459-468` and `BeaconDeviceHandler.performInitialization()`:

1. Connect
2. **`startNotifications`** on `…CB8`
3. wait 300 ms
4. **Clock sync** → `A5 FD <dow> <hh> <mm> <ss> 00×6 <ck>` — `dow` is **1–7, Monday = 1, Sunday = 7**
5. wait 300 ms
6. **Status query** → `A5 FC 01 00 00 00 A2`
7. wait 300 ms — then normal commands, ~150–200 ms apart

The clock sync matters: the device evaluates schedules against its own RTC, so without it the
time windows drift or never fire.

### ⚠️ The device drops an idle link in under 15 seconds

**Observed 2026-08-09 (#27).** A script that held a single connection across a 15-second idle
gap had its *next* write time out at the proxy — `Timeout waiting for
BluetoothGATTWriteResponse … after 30.0s` — with no disconnect surfaced to the caller
beforehand. Restructuring the same script to connect once per command fixed it outright.

So the connect-per-command transport in
[`../custom_components/kirri_beacon/device.py`](../custom_components/kirri_beacon/device.py)
is **required, not merely tidy**. Any design that holds a session open between user actions
will work for the first command and fail on the second.

This also quietly poisons *listening*: a long notification listen that sends nothing is not
connected for its full duration, however long the script thinks it waited. See the 21-byte
frame entry under **Still unknown** for a measurement this invalidated.

---

## Notifications (device → host)

**✅ Confirmed on the device 2026-08-08** — 4 writes, 4 notifications, through the ESPHome
proxy. See [`research/dossier.md` §7.4](research/dossier.md). The app's reliance on
notifications was the right inference, and the earlier §6.4 doubt is resolved in favour of
the pipe working.

⚠️ **The CCCD reads back `00-00` anyway.** It did before and it still does. On this device
the descriptor readback is a **false negative** — never use it to decide whether
notifications are enabled. Subscribe, send a valid frame, and see whether one arrives.

### 21+ byte frame — live status

| Offset | Field |
|---|---|
| 14 | intensity (low nibble, `& 0x0F`) |
| 15 | `isOn` — `0x01` = on |
| 16 | work status |
| 17–18 | work remaining, **big-endian uint16** |
| 19–20 | pause remaining, **big-endian uint16** |

Bytes 0–13 are not parsed by the app. Worth capturing and decoding ourselves.

### ⚠️ Unsolicited power-up frames — ✅ observed 2026-08-09 (#13)

**The device pushes frames of its own accord on the first connect after a power-up.** Captured
during the #13 power-cut recovery test, arriving immediately after `startNotifications` and
before any command was sent:

| Frame | Notes |
|---|---|
| `A5 FE 01 00 00 00 A4` | Sent **twice**. Command `0xFE` — listed below as *"defined, never sent"*, because the app never sends it. The **device** does. |
| `80 01 04 00 00 00 85` | **Header `0x80`, not `0xA5`.** A second frame family we had no idea existed. |

Both carry a **valid sum-mod-256 checksum** (`A5+FE+01 = 0x1A4 → A4`; `80+01+04 = 0x85`), so
they are genuine protocol frames rather than corruption. Their meaning is unknown — most
plausibly a boot/ready announcement, given when they appear.

**Design consequence, and it is not hypothetical.** An implementation that treats the first
notification after a write as the answer will read `A5FE01000000A4` as a schedule record. Drain
the queue before writing, and skip any notification that is not a 14-byte `A5FB` echo. The
integration does both, which is the only reason the power-cut recovery test passed first time.

### 14-byte frame beginning `A5 FB` — schedule echo

Same field layout as the `0xFA` write, with `FB` in place of `FA`:

```
A5 FB PW DY RC SH SM EH EM WH WL PH PL CK
```

This is a **readback path** — it means state can be genuinely synchronised rather than
guessed, so an HA integration need not be write-only/optimistic. **Confirmed in practice:**
every write is echoed with the full record, and a `0xFC` query returns the current one.

Two consequences worth designing around:

- **The echo is the only trustworthy confirmation.** An ATT-layer accept proves nothing on
  this device — a 0-byte write is accepted too (`research/dossier.md` §6.5). Treat a write as
  successful when its `A5FB` echo matches what was sent, not when the write call returns.
- **Changes made from the phone app are readable.** State set by the app was read back off
  the device, so an integration can detect out-of-band changes rather than overwriting them
  blindly.

---

## Ready-to-use frames

Generated by [`../custom_components/kirri_beacon/protocol.py`](../custom_components/kirri_beacon/protocol.py),
a clean-room re-implementation of the vendor's builder and the codec the integration actually
ships. Run `python3 tools/frames.py` to regenerate this table and self-check it, or
`python3 -m pytest` for the full suite — the spec is meant to be verifiable, not taken on trust.

| Action | Frame | Verified |
|---|---|---|
| **Power ON** (Delicate 6 s/120 s) | `A5 FA 01 7F 01 00 00 17 3B 00 06 00 78 F0` | ✅ **diffuser turned on** (nRF Connect) |
| **Power OFF** (`work = 0`) | `A5 FA 01 7F 01 00 00 17 3B 00 00 00 78 EA` | ✅ **diffuser stopped** — sent 2026-08-09 while record 2 was actively dispensing; echoed `A5FB…EB`, read back verbatim, mist stopped (#27 phase 4b) |
| ~~Power OFF (retired, pre-#27)~~ | `A5 FA 01 00 01 00 00 00 00 00 00 00 00 A1` | ⚠️ **echoed `A5FB…A2` and leaves the diffuser running** whenever records 2–4 hold anything — it clears record 1 instead of holding it, and the device falls through. The 2026-08-08 "diffuser stopped" observation was correct on a device where records 2–4 were empty. Kept as `protocol.RETIRED_OFF`; the vendor's app still sends it. |
| Intensity — Subtle (12 s) | `A5 FA 01 7F 01 00 00 17 3B 00 0C 00 78 F6` | ✅ echo `…F7` (2026-08-09) |
| Intensity — Radiant (18 s) | `A5 FA 01 7F 01 00 00 17 3B 00 12 00 78 FC` | ✅ echo `…FD` (2026-08-09) |
| Intensity — Intense (24 s) | `A5 FA 01 7F 01 00 00 17 3B 00 18 00 78 02` | ✅ echo `…03` (2026-08-09) |
| **Status query** — record 1 | `A5 FC 01 00 00 00 A2` | ✅ **returns record 1**, e.g. `A5FB017F010000173B003000781B` |
| Status query — record 4 | `A5 FC 04 00 00 00 A5` | ✅ returns `A5FB0100040000000000000000A5` — an **empty** record, correctly labelled (2026-08-09) |
| Clock sync (Fri 20:15:00) | `A5 FD 05 14 0F 00 00 00 00 00 00 00 CA` | ✅ accepted, **silently** — see below (2026-08-09) |

The three intensity frames are **echo-confirmed, not visually confirmed**: the device accepted
each one and echoed back the matching record. Nobody watched the mist to check that a 24-second
burst really lasts 24 seconds — that stays a human acceptance item in #13.

### ⚠️ The clock sync is never acknowledged

Sent to the device for the first time on 2026-08-09 (`A5FD07002804000000000000D5`, Sun
00:40:04). Result:

| | |
|---|---|
| Notification in reply | **None.** Nothing, ever — not an echo, not an ack |
| Device still responsive afterwards | ✅ yes, the next status query answered normally |
| Schedule record altered | ✅ no — byte-identical before and after |

**It is safe, and it is unconfirmable.** That combination matters more than it sounds:
`0xFD` is the one frame in this protocol with **no readback path**, so the "treat a write as
successful only when its echo matches" rule cannot apply to it. An implementation that waits
for an echo after the clock sync will stall for its full timeout on **every single connect** —
which, with clock sync in the connect sequence, means every command. Send it fire-and-forget.

Whether the device actually *applied* the time we sent is still unknown.

**What we do now know (2026-08-09, #27): the RTC is roughly right, and time windows really are
evaluated against it.** With all three of the app's schedules on the device, the only one
dispensing at ~13:40 local was record 2 (12:00–18:00); record 1 (10:00–12:00) and record 3
(18:00–22:00) sat idle, and record 1 kept sitting idle even while still enabled for all seven
days. That pins the device's clock to the correct hour and proves the window fields are not
decorative.

It does **not** show that our `0xFD` frame is what set the clock — the phone app had connected
minutes earlier and syncs on connect too. Attributing the correct time to *our* write needs a
device whose clock has been deliberately left wrong.

The OFF frame was independently re-derived by
[`../tools/frames.py`](../tools/frames.py) and by the ad-hoc script that sent it; both
produced `…A1`. Worth noting only because the checksum is the easiest thing in this protocol
to get quietly wrong.

Paste without spaces into nRF Connect's BYTE ARRAY field.

---

## Still unknown

- Bytes 0–13 of the 21-byte status notification — **and whether that frame exists at all on
  this device.** Every notification observed to date has been the 14-byte `A5FB` schedule
  echo: 4 in #5, 5 in #8, 7 more during #13 with the diffuser powered on and running, and a
  further 20 across the #27 investigation. **36 for 36, none of them 21 bytes.** The layout in
  this document is read out of the vendor's parser, not captured from the air.
  *Caveat before treating that as settled:* at Delicate the device dispenses ~6 s in every
  126 s, so a random sample is only ~5% likely to land during a burst — the absence is
  suggestive, not conclusive. A capture taken deliberately mid-dispense would settle it.
  **One was attempted on 2026-08-09 and the result is void.** A 150-second listen was held
  open with record 2 actively dispensing — long enough to guarantee at least one full burst
  inside the window — and nothing arrived. But that listen sent no writes, and the idle-link
  drop documented under *Connection sequence* means the link was almost certainly dead within
  ~15 s of the last query. It measured silence from a closed socket, not from the device.
  **A valid attempt must keep the link alive** — a status query every few seconds through the
  whole window. Until someone runs one, the 36-for-36 tally above contains **no deliberate
  mid-dispense sample at all**, which is the whole reason it stays suggestive rather than
  conclusive.
- ~~Whether the CCCD read-back of `00-00` prevents notifications, or is cosmetic.~~
  **Resolved 2026-08-08: cosmetic.** Notifications work regardless — dossier §7.4.
- ~~Whether `0xFC` can query records 2–4, and what it returns for an empty record.~~
  **Resolved 2026-08-09: it can**, and an empty record replies with a correctly-labelled
  zeroed frame — see *`0xFC` — status query*.
- ~~Is arbitration by record *id*, or last-write-wins?~~ **Resolved 2026-08-09: record-id
  priority.** Record 1 won a controlled test even when written *before* record 2 — see *It is
  record-id priority, not last-write-wins*.
- ~~Is `work = 0` accepted, and is it silent?~~ **Resolved 2026-08-09: accepted, stored
  verbatim, and silent**, measured against an actively-dispensing record 2. It is now the OFF
  frame — see *The vendor's own OFF frame does not switch the device off*.
- **Is the `PW` byte honoured in arbitration at all?** Every record ever read off this device
  has `PW = 0x01`, including empty ones and including the app's own "off": the app expresses
  off through the day mask, not through this flag. So "first *enabled* record" is stated on the
  strength of `createBeaconScheduleCommand` treating `PW` as an enable flag, and has never been
  isolated. The integration deliberately ignores it (`ScheduleRecord.matches_at`) — of the two
  untested readings, honouring a flag the device ignores would report *off* while the diffuser
  mists, which is the #27 failure again. Settle it by writing `PW = 0x00` with an otherwise
  live all-day record and watching for mist.
- **What does the device do at a window's exact boundary minute?** At 12:00, a 10:00–12:00 and
  a 12:00–18:00 record are both plausibly matching. Record-id priority makes the *answer* the
  same either way here, so nothing depends on it — but a lone record ending at 12:00 would
  reveal whether the end is inclusive. The integration treats both ends as inclusive, which is
  required anyway for a `00:00`–`23:59` record to cover the last minute of the day.
- **How is a window that runs backwards over midnight evaluated** (`22:00`–`06:00`), and which
  day does the mask then apply to? The app cannot produce one. The integration assumes it
  wraps, because the alternative silently disables a record the user believes is armed.
- Whether record `0x00` is really a master switch. **We will not be testing this.**
- What `0xFE` and the `0x80`-header frame mean. Both are pushed by the device on power-up
  (see above) and neither is ever sent by the app. Harmless to ignore, but they are the only
  evidence so far that this protocol has a device-initiated direction at all.
- What `0x06` does. Never sent by the app; not needed.

---

## Change log

| Date | Change | Source |
|---|---|---|
| 2026-08-08 | Initial spec written from the Aroma-Link reference. All 🔵 unverified. | #2 |
| 2026-08-08 | **Aroma-Link hypothesis disproved on-device**; GATT table shares nothing with it. | #3 |
| 2026-08-08 | **Full protocol recovered from vendor source maps.** Rewritten from scratch. | #3 |
| 2026-08-08 | **Notifications confirmed working** (4/4) through the proxy; §6.4 doubt resolved. OFF frame and status query verified on-device. CCCD readback recorded as a false negative. | #5 |
| 2026-08-09 | **Unsolicited power-up frames observed** — `A5FE01000000A4` (×2) and `80010400000085`, both checksum-valid, during the power-cut recovery test. First evidence of device-initiated frames and of a non-`A5` header. | #13 |
| 2026-08-09 | **Clock sync sent for the first time** — accepted, harmless, and **never acknowledged**. Three intensity presets echo-confirmed. Codec moved to `custom_components/kirri_beacon/protocol.py` with a pytest suite. | #13 |
| 2026-08-09 | **The 21-byte live-status frame was never observed, so the Work/Pause remaining sensors are not shipped.** #13 made them conditional on seeing one; nothing did. The decoder stays in `protocol.py` against the day one arrives, and `device.py` logs any notification of that length at `info` rather than swallowing it. | #13 |
| 2026-08-09 | **`WH WL` confirmed to be literal seconds, by eye** — an Intense burst (`work = 0x0018` = 24) was watched and lasted 24 seconds. Until now the unit was only inferred from the vendor's parser; every preset had been echo-confirmed but none visually. Custom values verified the same way end to end: `A5FA017F010000173B002800C862` (`work = 0x0028` = 40 s, `pause = 0x00C8` = 200 s) was written and echoed back byte-for-byte. | #13 |
| 2026-08-09 | **Record arbitration established on-device.** The device runs the first *enabled* record by id whose day mask **and** time window match, and **falls through** rather than dispensing on the union — so clearing a record hands control to the next one instead of switching the device off. Also: `0xFC` queries any record and empty records reply with a labelled zeroed frame; the device drops an idle link in under 15 s; the phone app's three default schedules occupy records 1–3 and are re-pushed on every connect. The OFF recipe is annotated in both places it appears, and the 150-second mid-dispense listen is recorded as void rather than as evidence. | #27 / #28 |
| 2026-08-09 | **Arbitration isolated as record-*id* priority, and `work = 0` confirmed accepted and silent.** Record 1 won even when written *before* record 2, so an always-matching record 1 holds against anything the app writes. **The OFF frame is now `A5FA017F010000173B00000078EA`** — record 1 held rather than cleared, with a zero dispense time — measured against an actively-dispensing record 2. The retired record-clearing frame is documented as the vendor's own bug and kept as `protocol.RETIRED_OFF`. `PW`, boundary minutes and midnight-wrapping windows are added to *Still unknown* as the assumptions the new matcher rests on. | #27 |
