# Research dossier — Kirri Beacon

Everything known about the device before it was ever observed on air, plus the record of
what was actually observed. **Sections marked 🔵 are inference; sections marked ✅ were
verified against a primary source.** Keep that distinction — it is the whole reason this
file exists.

---

## 1. Device

| | |
|---|---|
| Product | **Kirri Beacon** |
| SKU | SAH5910 |
| Price | A$350 |
| Vendor | Scent Australia Home (Kirri is the retail brand) |
| Power | **Mains, no battery** — it needs an outlet wherever you put it |
| Pairing | Kirri docs: *"hold the Bluetooth button for 5 seconds to connect"*, *"place your mobile within one metre of the diffuser"* |
| Scheduling | Kirri docs: *"Schedule up to **5 intervals** for working and pause times"* |
| Intensity | Kirri docs: *"Adjust fragrance strength by setting the **run and pause times** within the app. Longer run times increase the scent's intensity."* → **no separate intensity control** |

Source: Kirri's own product and training pages.

### ~~Why the "5 intervals" line matters~~ ❌ RETRACTED 2026-08-08

> **This was wrong, and it is worth leaving in as a lesson.**
>
> I originally argued: Aroma-Link's schedule payload has exactly 5 slots, Kirri's marketing
> says "up to 5 intervals", therefore two independent sources agree and the Aroma-Link
> hypothesis is corroborated.
>
> The vendor's own source code says the Beacon has **4 user-writable schedule records**
> (`0x01`–`0x04`), plus a record `0x00` the app deliberately never touches. The "5" in the
> marketing copy has nothing to do with Aroma-Link's 5-slot structure.
>
> **What went wrong:** I went looking for confirmation of a hypothesis I already favoured, and
> a loose numeric coincidence was enough to satisfy me. It made a guess feel like a finding,
> and that false confidence survived until the device itself contradicted it. Numeric
> coincidences between marketing copy and a binary layout are worth roughly nothing —
> a matching *structure* would have been evidence; a matching *integer* was not.

### Open risk — the physical Bluetooth button ⚠️

If the 5-second hold is needed only for **initial pairing**, everything below works.

If the Beacon **bonds to a single central** and requires the button to accept a new one,
then Home Assistant and the phone app cannot share the device at all — that would be a
**device limitation**, not something a better implementation can route around. Issue #3
tests this in 30 seconds (try connecting with nRF Connect *without* touching the button);
issue #8 settles it.

---

## 2. Product lineage 🔵

Kirri is a rebrand of **Scent Australia Home**. Its predecessor Android app,
`com.dewoo.lot.scentaus.uat`, was a white-label of **Aroma-Link** — the OEM platform from
**Shenzhen Kerima Technology** (app publisher name "Dewoo"). The current app is
`com.scentaustralia.kirri`.

**Status: inference.** It is strong — app-package lineage plus the 5-interval match — but
**no Kirri device has ever been observed on air in any public source.** Issue #3 converts
this into fact or kills it.

---

## 3. Protocol families — the decision table ✅

Six BLE diffuser families are implemented by `mr-sparks/scent-assistant`. Which one the
Kirri belongs to is decided by its GATT characteristic set:

| GATT shows… | Family | Consequence |
|---|---|---|
| `FFF0` + write `FFF2` + notify `FFF1` (+ indicate `FFF3`) | **Aroma-Link** ⭐ expected | Supported today — project is config only |
| `FFF0` + a **single** char `FFF6` | Scent Marketing AK / AromaTech | Supported; ⚠️ needs a `0x8F` + `"8888"` login before *any* write is accepted |
| `FFE0` + `FFE1` / `FFE2` | Scent Tech / Aromely | Supported |
| `0180` + `DEAD` | Scentiment (JSON over BLE) | Supported |
| `EE01` + `EE02` / `EE03` | Scent Marketing GW | Supported |
| `A201` + `2B10` / `2B11` | Genuine encrypted Tuya | ⚠️ worst case — needs a cloud-derived `local_key` |
| none of the above | Novel protocol | Triggers the reverse-engineering fallback (#12) |

**The one ambiguity UUIDs alone cannot resolve:** `FFF0`/`FFF1`/`FFF2` is used by both
Aroma-Link *and* unencrypted Tuya MCU-serial. Enable notifications on `FFF1` and read the
first bytes — `A5 AA AC…` ⇒ Aroma-Link, `55 AA…` ⇒ Tuya MCU-serial.

The `scent-assistant` snippets in this section are quoted from
[mr-sparks/scent-assistant](https://github.com/mr-sparks/scent-assistant), which is
**MIT-licensed** (§5) — the copyright and licence notices are the upstream repository's, and the
quotations here are its constants, not our code.

Verified from `scent-assistant/custom_components/scent_assistant/const.py:24-27`:

```python
SERVICE_UUID        = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID     = "0000fff2-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID    = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_INDICATE_UUID  = "0000fff3-0000-1000-8000-00805f9b34fb"  # Aroma-Link only
```

### Advertised-name patterns ✅

From `const.py:191-201`. Note the **trailing space** on `"Scent "` — `Scent ` is
Aroma-Link, `Scent-` (hyphen) is a different family entirely. Record the exact character
after "Scent".

```python
BLE_NAME_PATTERNS = {
    DeviceType.AROMA_LINK:      ["Scent ", "DAP.A5"],
    DeviceType.TUYA_BLE:        ["BT-ivy"],
    DeviceType.SCENTIMENT:      ["Scentiment"],
    DeviceType.AROMELY_ARO_MAX: ["DiffuserAroMax", "DiffuserAro"],
}
```

### Manufacturer-ID hints ✅

| Company ID | Family |
|---|---|
| `22851` / `0x5943` | Scent Marketing AK |
| `17932` / `0x460C`, `61441` / `0xF001` | Scent Marketing GW |
| `2000` / `0x07D0` | Genuine Tuya |

---

## 4. Expected protocol — Aroma-Link ✅

Full spec lives in [`../protocol.md`](../protocol.md). Summary:

```
A5 AA AC <xor> <payload…> C5 CC CA
         ^^^^^ XOR over the payload bytes only (not the header/footer)
```

Sub-commands: `0x08` power · `0x03` fan · `0x0D` device info · `0x16` schedule ·
`0x17` time sync.

---

## 5. Reference repositories ✅

All six confirmed to exist via the GitHub API.

| Repo | Why it matters |
|---|---|
| [mr-sparks/scent-assistant](https://github.com/mr-sparks/scent-assistant) | **The key one.** MIT, HACS, six BLE families incl. Aroma-Link |
| [ttrushin/ha-aromatech-scent-diffuser](https://github.com/ttrushin/ha-aromatech-scent-diffuser) | AK family reference |
| [Smart-Techno1ogy/Scent_Tech](https://github.com/Smart-Techno1ogy/Scent_Tech) | Scent Tech / `FFE0` family |
| [acamb/aroma-http](https://github.com/acamb/aroma-http) | Independent Aroma-Link implementation — useful cross-check |
| [Scentrealm/Bluetooth](https://github.com/Scentrealm/Bluetooth) | Vendor-published BLE docs |
| [PlusPlus-ua/ha_tuya_ble](https://github.com/PlusPlus-ua/ha_tuya_ble) | For **elimination** — confirms/rules out genuine Tuya |

### What was verified in `scent-assistant`'s source, and why

| Claim | Evidence | Verdict |
|---|---|---|
| Works through an ESPHome bluetooth_proxy | `device.py:286` — `bluetooth.async_ble_device_from_address(hass, addr, connectable=True)` then `establish_connection(...)` | ✅ the 3-floor topology is viable |
| Connects on demand, then disconnects | `device.py:455-494` — `_schedule_disconnect()` / `_delayed_disconnect()` | ✅ app coexistence is designed for |
| An unknown device can still be tried as Aroma-Link | `config_flow.py:153` — `dtype or DeviceType.AROMA_LINK`, and the picker lists **every** named device | ✅ hypothesis testable with **zero** code changes |
| **Setup can discover through a proxy** | `config_flow.py:8,129` — raw `from bleak import BleakScanner` / `BleakScanner.discover(...)`, and **no** `async_step_bluetooth` handler | ❌ **it cannot** — see below |

### ⚠️ The one real defect found

`config_flow.py` scans with **raw `BleakScanner`**, bypassing HA's Bluetooth manager. It
therefore only sees the HA host's own adapter and **cannot see through a bluetooth_proxy**
— so the setup wizard will show an **empty list** for a device 3 floors away, even though
runtime control would work perfectly once the entry exists.

**Workaround (zero code):** carry the Beacon downstairs, plug it in near the HA box, pair
within one metre, complete the config flow, then carry it back up. The MAC does not change,
and runtime goes through the proxy. → issue #6.
**Proper fix:** make the config flow proxy-aware upstream. → issue #11.

---

## 6. Observed on air ✅ complete

Recorded 2026-08-08 with nRF Connect for Android, standing next to the diffuser with the Kirri
app force-closed. Raw session log:
[`../captures/2026-08-08-nrf-connect-gatt-session.txt`](../captures/2026-08-08-nrf-connect-gatt-session.txt)
(MAC redacted there, as it is here — see §6.1 below and [`../privacy.md`](../privacy.md)).

This section is the deliverable of issue #3 and closed it out.

### 6.1 Advertisement ✅ observed 2026-08-08, nRF Connect for Android

| Field | Observed |
|---|---|
| Complete Local Name | **`AJBLE100`** — exact, no whitespace |
| MAC address | `11:22:33:44:55:66` — redacted, see below |
| RSSI (close range) | −64 to −76 dBm, typical −69 |
| Advertising interval | 320 ms |
| Advertising type | Legacy |
| Connectable | Yes |
| Flags | `0x05` = LE **Limited** Discoverable + BR/EDR Not Supported |
| Manufacturer data | **none present** |
| Company ID | n/a — no manufacturer data |
| Advertised service UUIDs | **`0xFFF0`** (incomplete list) |
| Bond state | NOT BONDED |

> **The MAC is a placeholder throughout this document.** The real one is not published — it
> is a network identifier for a device in someone's house. `11:22:33:44:55:66` was not picked
> at random, though: the **⚠️ Watch the MAC** note below turns on two bit-level properties of
> the first octet, and `0x11` has both of them, exactly as the real first octet did.
> Substituting `AA:BB:CC:DD:EE:FF` — the placeholder the config-flow help text uses —
> would have left that paragraph arguing from digits that contradict it. **Your diffuser's
> address will be different**; read it off a scanner, and see the amendment below on why the
> low octets are junk anyway.

> #### ⚠️ Amendment — 2026-08-08, re-observed through the ESPHome proxy (#4)
>
> The table above is what **nRF Connect** shows. nRF Connect merges the scan response into
> the advertisement, so it flatters the picture. Seen on the **raw** advertisement path
> through the proxy — which is what Home Assistant actually consumes — the packet is
> *much* barer:
>
> ```
> 11:22:33:44:55:66   rssi -54   20 packets / 20 s   address_type = 0 (public)
>   0x01 flags            05
>   0x02 uuid16-partial   f0 ff
> ```
>
> **There is no local name on air.** Not once in 20 packets. `AJBLE100` lives in the scan
> response and in GATT `0x2A00` — neither of which reaches an HA discovery matcher.
>
> **Two consequences:**
>
> 1. **Discovery in #13 cannot match on `local_name`.** Point 2 below proposes adding
>    `"AJBLE100"` to a name-pattern list; that advice is **wrong for the proxy path**, and
>    would have failed in a way that looks like a broken proxy. The only on-air
>    discriminator is `0xFFF0` — which this device advertises and
>    [does not implement](#the-advertised-vs-actual-mismatch), and which is shared with a
>    large population of cheap BLE devices. So: match on address, or match `FFF0` and
>    *confirm* by reading the vendor service after connecting.
> 2. **`address_type = 0`, i.e. public** — even though `11:22:…` has the I/G bit set and is
>    not a valid public IEEE address. Worth knowing before diagnosing a failed connect as a
>    protocol problem.

**Three things to take from this:**

1. **`0xFFF0` is advertised.** That is the expected Aroma-Link service UUID. Consistent
   with the hypothesis — but `FFF0` is shared with unencrypted Tuya MCU-serial, so it is
   not yet decisive. §6.2/§6.4 settle it.
2. **The name is `AJBLE100`, not `Scent ` or `DAP.A5`.** It does **not** match any pattern
   in `scent-assistant`'s `BLE_NAME_PATTERNS`, so **auto-detection will not fire.** The
   device must be picked manually in the config flow, where it defaults to `AROMA_LINK`
   (`config_flow.py:153`) — which is what we want anyway. → gives issue #10 its concrete
   payload: add `"AJBLE100"` to the Aroma-Link patterns plus a manifest matcher.
3. **No manufacturer data**, so no company-ID shortcut. The AK / GW / Tuya company IDs are
   all ruled out by absence.

**⚠️ Watch the MAC.** `11:22:33:44:55:66` is not a valid public IEEE address — the I/G bit
of the first octet is set (`0x11 & 0x01 = 1`), which would mean multicast. Its top two bits
are `00`, which in BLE terms is the *non-resolvable private address* pattern. Most likely a
cheap module with a hardcoded junk address reported as public, which is harmless. But HA
and ESPHome pin the device **by MAC**, so if it ever rotates the integration breaks.
**Re-check this MAC after a power-cycle** and record the result:

| Check | Result |
|---|---|
| MAC unchanged after diffuser power-cycle? | |

> macOS will **not** show you the MAC — it hides it behind a CoreBluetooth UUID. Get it
> from the phone.

### 6.2 GATT table ✅ observed 2026-08-08 — **the Aroma-Link hypothesis does not survive this**

Connected at 19:31:27, services discovered cleanly. Connection parameters: interval 37.5 ms,
latency 0, supervision timeout 5000 ms.

| Service | Characteristic | Props | Descriptors |
|---|---|---|---|
| Generic Access `0x1800` | Device Name `0x2A00` | R | |
| | Appearance `0x2A01` | R | |
| | Peripheral Preferred Conn Params `0x2A04` | R | |
| **`edfec62e-9910-0bac-5241-d8bda6932a2f`** | `0783b03e-8535-b5a0-7140-a304d2495cb8` | **N** | CCCD `0x2902`, User Description `0x2901` |
| | `0783b03e-8535-b5a0-7140-a304d2495cba` | **W, WNR** | CCCD `0x2902`, User Description `0x2901` |

**There is no `FFF0` service in the GATT table**, despite `0xFFF0` being advertised. There is
no `FFF1`, `FFF2` or `FFF3`. Against the §3 decision table this is **"none of the above"** —
a novel protocol, not one of the six known families.

#### What it *is*

A textbook **serial-port-over-BLE** layout: one vendor service, one **notify** characteristic
(device → host) and one **write / write-without-response** characteristic (host → device).
The two characteristic UUIDs differ only in the final byte (`…cb8` / `…cba`), so they are one
vendor allocation. Same shape as Nordic UART, different UUIDs.

- **Write commands to** `0783b03e-8535-b5a0-7140-a304d2495cba`
- **Subscribe for replies on** `0783b03e-8535-b5a0-7140-a304d2495cb8`

None of these are RFC-4122-conformant UUIDs (the variant nibbles `5…` and `7…` are invalid),
i.e. they're 16 ad-hoc random bytes rather than generated by a UUID library. Cosmetic, but it
points at a small firmware shop rather than a large OEM platform.

#### The advertised-vs-actual mismatch

Advertising `0xFFF0` while serving a completely different service is most likely a leftover
default in the BLE module's config that nobody changed. It is worth noting because **it is
exactly the kind of thing that would have sent us down the wrong path** if we had trusted the
advertisement and skipped the GATT dump.

⚠️ **Ruled out / to rule out:** Android caches GATT tables per device, so a partial table can
be a measurement artefact. Refresh the device cache in nRF Connect and re-read before treating
this table as final.

| Check | Result |
|---|---|
| Table confirmed by full expansion? | ✅ yes — two services, nothing hidden |
| `0x2901` User Description on `…cb8` | **empty** |
| `0x2901` User Description on `…cba` | **empty** |
| `0x2A00` Device Name | not read |
| `0x2A01` Appearance | not read |

Also absent: **no Device Information service (`0x180A`)** — so no firmware, model or
manufacturer string is exposed anywhere on the device.

#### Does the *frame format* still survive?

Separate question from the transport. A vendor can swap the BLE module — changing every UUID —
while keeping the same application-layer protocol. So the Aroma-Link **frame format** may still
apply even though its **UUIDs** clearly don't. Test by writing the Aroma-Link power-on frame to
`…cba`:

| Frame written to `…cba` | Device reaction | Notify response on `…cb8` |
|---|---|---|
| `A5AAAC5E570801C5CCCA` | | |

### 6.3 Connection behaviour ✅ observed 2026-08-08

Full session log: [`../captures/2026-08-08-nrf-connect-gatt-session.txt`](../captures/2026-08-08-nrf-connect-gatt-session.txt)

| Question | Answer |
|---|---|
| Connected? | ✅ `connectGatt(autoConnect=false, TRANSPORT_LE, LE 1M)`, status 0, **3.1 s** to connect |
| **Bonding required?** | ❌ **No** — state stayed `CONNECTED / NOT BONDED` for the whole 19-minute session |
| Any PIN / passkey prompt? | ❌ none |
| Link stability | **Held ~19 min (19:31 → 19:50) with no disconnect** — a genuinely good sign |
| `0x2A00` Device Name | `AJBLE100` — identical to the advertised name, no extra information |
| `0x2A01` Appearance | `0x0000` = **Unknown** — the vendor never set it |
| `0x2A04` Preferred conn params | interval 20–30 ms, slave latency 16, supervision timeout 6000 ms |
| Firmware version | **not exposed anywhere** — no Device Information service |
| Did it connect without the Bluetooth button? | *(pending — first attempts failed, need to know what fixed it)* |
| Does it beep on an accepted write? | *(pending)* |

**No bonding is a meaningful de-risk for [ADR-006](../decisions.md).** The worst case for app
coexistence was the Beacon bonding to a single central and gating new connections behind the
physical button. It does not bond at all — so that specific failure mode is off the table.
The button question is still open, but its scariest version just died.

The 19-minute uninterrupted connection also says the radio link is solid at ~−69 dBm, which
bodes well for the ESP32 proxy sitting much closer.

### 6.4 Notifications ✅ RESOLVED — reading 2 was correct, the pipe works

> **Resolved 2026-08-08 — see [§7.4](#74-notifications-work--resolves-64).** Notifications
> **do** work. The CCCD still reads back `00-00`, exactly as recorded below, and it makes no
> difference: 4 writes, 4 notifications. Reading 2 (sloppy firmware that hardcodes the CCCD
> read) is confirmed and reading 1 is dead.
>
> **The "plan for reading 1" instruction at the end of this section is withdrawn.** An
> integration built on it would have been write-only with optimistic state, which is
> materially worse and entirely unnecessary.
>
> The rest of this section is left exactly as written — it is an accurate record of the
> observation and of a reasonable inference that turned out to be wrong. See the note at the
> end for why it was wrong.

**Zero notifications were received in the entire 19-minute session**, including after both
command writes.

More interesting is *why* that might be. The CCCD write is accepted but **never reads back**:

| Time | Event | CCCD value |
|---|---|---|
| 19:40:24.923 | wrote `0x0100` → "Data written … 01-00" | ✅ accepted |
| 19:41:13.709 | read back | ❌ **`00-00`** |
| 19:41:17.272 | wrote `0x0100` again → accepted | ✅ |
| 19:41:23.573 | read back | ❌ **`00-00`** |
| 19:45:12.723 | wrote `0x0100` again → accepted | ✅ |

Every single CCCD read in the session returned `00-00`, *including immediately after a
successful write of `01-00`*. The firmware takes the write and discards it.

**Two readings, and we cannot yet distinguish them:**

1. **Notifications genuinely never enable** — the CCCD is decorative and the notify
   characteristic is dead. If so, any future integration must be **write-only with optimistic
   state**: no readback, no confirmation that a command landed, no detection of changes made
   from the phone app. That is a significant architectural constraint and it would be much
   better to know now than after building on the assumption of feedback.
2. **Sloppy firmware that hardcodes `00-00` on read** but wires notifications up internally,
   and stays silent only because it never received a frame it understood. Common in cheap
   BLE stacks.

Reading 2 is resolved the moment we send a *valid* command and get anything back. ~~Until
then, plan for reading 1.~~

> **Why this section reached the wrong conclusion.** The test that resolved it is named
> correctly right there in the line above — *"send a valid command and get anything back"* —
> and then the section defaults to the pessimistic reading instead of running it. The only
> frames sent during this session were Aroma-Link frames, which [§6.5](#65-live-write-test--attempted-2026-08-08)
> shows the device ignores entirely. **Silence in response to a command the device cannot
> parse is not evidence about the notification pipe.** It is evidence about the command.
>
> The generalisable error is treating *absence of a response* as a property of the transport
> when the payload was never valid. Two negatives — a CCCD that won't read back and a device
> that won't answer — looked mutually corroborating and were in fact independent, one real
> and one an artefact of sending the wrong bytes.

### 6.5 Live write test ✅ attempted 2026-08-08

Wrote the Aroma-Link power-on frame to `0783b03e-…cba`:

```
A5 AA AC 5E 57 08 01 C5 CC CA
```

| Time | Written to `…cba` | ATT result | Notify response |
|---|---|---|---|
| 19:44:18 | `A5AAAC5E570801C5CCCA` | **accepted, no error** | none |
| 19:49:28 | *(empty — 0 bytes)* | **accepted, no error** | none |
| 19:50:37 | `A5AAAC5E570801C5CCCA` | **accepted, no error** | none |
| — | Physical device reaction to the Aroma-Link frames | **none** | |

**Then, with the real protocol** (recovered from the vendor's source — see
[`../protocol.md`](../protocol.md)):

| Written to `…CBA` | ATT result | Physical reaction |
|---|---|---|
| `A5FA017F010000173B00060078F0` | accepted | ✅ **the diffuser turned on** |

That single write closes the recon phase. The protocol is no longer a hypothesis: it was read
out of vendor source *and* confirmed on the device, with no bonding, no handshake, no login
frame, and no ESP32 in the path yet.

**A 0-byte write was accepted too.** So the GATT server does no length or content validation
at the ATT layer — it will swallow literally anything. That kills "the write succeeded" as
evidence of anything at all, and it means we get **no error channel** to probe with. We cannot
learn the protocol by watching what gets rejected, because nothing gets rejected.

**What "accepted" does and doesn't mean.** A clean ATT write only proves the characteristic
took the bytes. It says nothing about whether the firmware parsed them. A device that ignores
an unrecognised frame and a device that acts on it look identical at this layer — which is why
the physical reaction is the only observation that counts here.

Absent a device reaction, the reading is: **the Aroma-Link frame format did not survive
either.** Not just different UUIDs — a different protocol. That moves the project to the
reverse-engineering path (#12), and makes the *Kirri app itself* the only remaining source
of truth.

### 6.6 Verdict ✅ settled 2026-08-08

| | |
|---|---|
| Protocol family | **None of the six.** A vendor-specific serial-over-BLE protocol |
| Matches the Aroma-Link hypothesis? | ❌ **No** — different UUIDs, different framing, different checksum |
| How it was solved | **Not** by sniffing. The APK is a Capacitor/Ionic hybrid that shipped **unminified source maps**, so the complete original TypeScript — including every protocol constant — was recoverable by unzipping it |
| Full spec | [`../protocol.md`](../protocol.md) — recovered from vendor source, not inferred |
| Next issue | #4 (flash the ESP32 proxy). #6/#7 (`scent-assistant`) are **dead** — it does not implement this protocol. #12 (sniffing) is **unnecessary** |

**The three-line summary of the real protocol:**

```
A5 <cmd> <data…> <checksum = sum of all preceding bytes & 0xFF>
0xFA = write schedule record   ← power, intensity and scheduling are all this one command
0xFC = query status · 0xFD = clock sync · 0xFB = notification echo
```

#### Why the original hypothesis failed, and what actually broke it

The Aroma-Link inference was built on app lineage — a predecessor package name that was a
genuine Aroma-Link white-label. That evidence was real, and it was still misleading: the app
supports **two** device families, and the *other* one (Element, on `FFF0`/`FFF1`/`FFF2`) is
essentially Aroma-Link. The lineage was right about the vendor and wrong about our model.

The device then reinforced the error by **advertising `0xFFF0` while serving nothing of the
sort** — almost certainly an advertising config copied from the Element product and never
updated. Had we trusted the advertisement instead of dumping the GATT table, we would have
spent far longer on the wrong protocol.

**What actually resolved it was reading the vendor's code, not observing the device.** Every
on-air observation up to that point was either ambiguous or actively wrong.

---

## 7. Verified through the ESPHome proxy ✅ 2026-08-08 (#4)

Everything above was observed from an Android phone sitting next to the diffuser. This
section re-verifies the parts that matter over the path the integration will actually use:
**remote API client → ESPHome proxy → Kirri**.

### 7.1 Active GATT works end to end ✅

```
[bluetooth_proxy]  [0] [11:22:…] Connecting v3 without cache
[esp32_ble_client] [0] 0x00 Connecting          <- 0x00 = public address type
[esp32_ble_client] [0] Connection open          <- MTU 251
[esp32_ble_client] [0] Service discovery complete
```

Full service enumeration succeeded; the vendor service and both characteristics are present
and carry the expected properties (see [`../protocol.md`](../protocol.md) for the handle
map). Disconnect was clean and connection slots returned to 3 of 3 — the
[upstream slot-leak bug](https://github.com/home-assistant/core/issues/176516) did **not**
bite.

**This retires the largest remaining architectural risk.** #13 has no transport unknowns
left; what remains is protocol logic, which is already written down.

### 7.2 The physical Bluetooth button is *not* required 🎉

[ADR-006](../decisions.md) flagged this as the risk that no implementation could engineer
around: if the Beacon gated new connections behind a 5-second button hold, HA and the phone
app could not share it.

**Evidence it doesn't.** The first connect attempt *failed*, and the failure log is what
settles it:

```
[esp32_ble_client] [0] 0x00 Connecting
[esp32_ble_client] [0] Disconnecting (conn_id: 0).      <- 1.5s later, no "Connection open"
```

Reaching that `Disconnecting` line requires `want_disconnect_`, and per
`ble_client_base.cpp:328` that branch is only taken when `param->open.status` was **OK** —
the error path returns earlier. So **the device accepted that connection too**, with nothing
touched and no button pressed. The teardown was ours, not the Kirri's.

Still open for #8: whether this survives a power-cycle, and whether the app and HA can hold
the link alternately. But the "button gates everything" scenario is dead.

### 7.3 ⚠️ Gotcha — the proxy has ONE bluetooth subscriber slot

This cost an hour and will cost it again if it isn't written down.

`BluetoothProxy::subscribe_api_connection()` keeps a **single** `api_connection_`, and
**only** `SubscribeBluetoothLEAdvertisementsRequest` claims it (`api_connection.cpp:1230`).
All proxy responses — including connect results — are delivered to whoever holds the slot,
and the newest subscriber silently displaces the previous one.

So a standalone debug script that connects to the proxy and issues a GATT connect *without*
first subscribing to advertisements will:

1. have its connect request executed correctly, and
2. have the **response delivered to Home Assistant instead**, and
3. time out, while HA drops a connection it never asked for.

The symptom — a connect that times out client-side while the proxy log shows a successful
open followed by an unexplained disconnect — looks exactly like a broken device. It isn't.
**Subscribe to raw advertisements first.** And be aware that doing so displaces HA until the
node is restarted.

---

### 7.4 Notifications work ✅ resolves §6.4

Run 2026-08-08 through the ESPHome proxy — **the first write to the device through our own
stack**, rather than nRF Connect by hand as in [§6.5](#65-live-write-test--attempted-2026-08-08).
Notifications were enabled on handle 10 (`…CB8`), then a status query and two schedule writes
were sent to handle 14 (`…CBA`).

| → sent to `…CBA` | ← notification on `…CB8` |
|---|---|
| `A5FC01000000A2` status query | `A5FB017F010000173B003000781B` |
| `A5FA0100010000000000000000A1` off | `A5FB0100010000000000000000A2` |
| `A5FA0100010000000000000000A1` off (repeat) | `A5FB0100010000000000000000A2` |
| `A5FC01000000A2` status query | `A5FB0100010000000000000000A2` |

**Four writes, four notifications.** No dropped responses, no timeouts.

> ⚠️ **Annotation added 2026-08-09 (#27): the frame labelled `off` above does not reliably turn
> the device off.** It clears record 1, and a cleared record matches nothing — so the device
> *falls through* to whatever the phone app left in records 2–4 and keeps dispensing. It
> genuinely did stop the diffuser on the day this was captured, because records 2–4 were empty
> then. The label is left as it was written; what changed is what we know it means. The
> integration now sends `A5FA017F010000173B00000078EA` instead — record 1 *held* with a zero
> dispense time. See [`../protocol.md`](../protocol.md#-the-vendors-own-off-frame-does-not-switch-the-device-off).

**The CCCD still reads back `00-00`.** Both things are true at once, which is the whole
lesson of §6.4: on this device the CCCD readback is a **false negative** and must never be
used as a health check for the notify pipe. Enable notifications and see whether frames
arrive; do not ask the descriptor whether it worked.

#### What the status response decodes to

`A5 FB │ 01 7F 01 00 00 17 3B 00 30 00 78 │ 1B` — the [§0xFA layout](../protocol.md) with
`FB` in place of `FA`:

| Field | Value |
|---|---|
| `PW` / `DY` / `RC` | on · `0x7F` all days · record 1 |
| Window | 00:00 – 23:59 |
| Work / pause | **`0x0030` = 48 s** / `0x0078` = 120 s |

Checksum `1B` verifies (sum = 795, `& 0xFF` = `0x1B`), as does `A2` on the off echo
(sum = 418).

**48 s is not a value this project ever wrote.** §6.5's frame set work to 6 s. So the 48 s
came from the phone app, and we read it back off the device — which is the single most useful
consequence of this section: **state written by the app is visible to us.** An integration can
detect out-of-band changes rather than silently overwriting them.

It also explains a real-world symptom: 48 s is the top of the range the app offers, so the
diffuser had been running at maximum dispense continuously. The room said so before the
protocol did.

#### Consequences for #13

| Previously assumed | Actually true |
|---|---|
| Write-only, optimistic state | **Real state sync** — query on connect, trust the echo |
| No confirmation a command landed | **Every write is echoed**, and the echo carries the full record |
| App changes invisible | **App changes are readable** |

Also worth designing around: an ATT-layer accept means nothing here — §6.5 established that a
0-byte write is accepted too. The `A5FB` echo is the *only* trustworthy confirmation that a
command was understood, so a write should be considered successful when its echo matches, not
when `bluetooth_gatt_write` returns.

One caveat on the status query: only record 1 was ever queried. Whether `0xFC` can address
records 2–4, and what it returns for an empty record, is untested.
