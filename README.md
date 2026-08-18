# Kirri Beacon for Home Assistant

Control a **Kirri Beacon** aroma diffuser (SKU SAH5910, Scent Australia Home) from Home
Assistant, locally over Bluetooth LE. No cloud, no vendor account, no phone app.

Power, intensity and timing become ordinary Home Assistant entities, so the diffuser can be
automated like anything else in the house.

> **Beacon only.** Kirri's other models are not supported — the **Element** speaks a
> different protocol on different UUIDs, and Macro and Pillar are untested. Check the SKU on
> the box before you start.

## What you get

| Entity | What it does |
|---|---|
| `switch.<name>` | Power on / off |
| `select.<name>_intensity` | Delicate · Subtle · Radiant · Intense. `Custom` appears, read-only, when the timings match no preset |
| `number.<name>_dispense_time` | Seconds of mist per burst — 3 to 48 |
| `number.<name>_pause_time` | Seconds between bursts — 10 to 3600 |
| `button.<name>_sync_clock` | Push Home Assistant's time to the device |
| `sensor.<name>_failed_polls` | Diagnostic: status polls that failed since the integration loaded |
| `sensor.<name>_echo_timeouts` | Diagnostic: attempts the device never confirmed — reads and writes alike. A command that goes unanswered twice counts twice, whether or not the retry then rescued it |

> On the Beacon, **intensity *is* the dispense/pause ratio** — there is no separate intensity
> register. The presets set both numbers (6 / 12 / 18 / 24 seconds of mist against a 120
> second pause); the two number entities set them by hand.

## Before you install

| | |
|---|---|
| **A Kirri Beacon** | SKU SAH5910 |
| **Home Assistant** | Any version with Bluetooth support. From 2026.3 you get the Kirri logo on the integration page; on older cores it is a grey placeholder and nothing else differs. |
| **A Bluetooth route that reaches the diffuser** | Home Assistant's own adapter if the unit is in range, otherwise a **BLE proxy** beside it — an ESPHome `bluetooth_proxy` board, a Shelly, anything Home Assistant lists. The route must be **connectable**, not advertisement-only. |
| **ESPHome ≥ 2026.5.0** | Only if the proxy is an ESPHome board — but then it is **not optional**. Earlier releases let sustained WiFi traffic break GATT connects with `status=0x85`, the infamous "error 133". It looks exactly like a protocol bug and will cost you a day. |

A ready-to-flash proxy config is in [`esphome/kirri-proxy.yaml`](esphome/kirri-proxy.yaml),
with flashing steps and post-flash checks in [`esphome/README.md`](esphome/README.md). It
holds no Kirri logic — it is a stock transparent proxy carrying GATT over WiFi.

## Install

### HACS

**HACS → ⋮ → Custom repositories** → add `https://github.com/luguina/kirri-beacon-hass`,
category **Integration** → **Download** → **restart Home Assistant**.

### Manually

```sh
mkdir -p <your-ha-config>/custom_components
cp -r custom_components/kirri_beacon <your-ha-config>/custom_components/
```

Then **restart** Home Assistant — a restart, not a config-entry reload. A reload re-runs
setup without re-importing the Python, so new code will not be there.

### Add the diffuser

**Settings → Devices & services → Add integration → Kirri Beacon.** It is usually discovered
on its own; if not, enter its Bluetooth address by hand. Setup connects once to confirm the
device really is a Beacon rather than trusting the address.

> **You will see this offered for devices that are not a Kirri.** The Beacon puts only the
> generic service UUID `0000fff0-…` on air, which it shares with a large population of cheap
> BLE hardware. Home Assistant offers those too; setup then connects, finds no Kirri service,
> and stops with *not supported*. That is expected — the alternative is connecting to every
> FFF0 device in range, which is worse.

Turn the switch on and listen for the unit to start misting. That is the whole check.

## Before you automate it

### A power cut needs a human

**The Beacon does not come back on when mains is restored.** It returns in the OFF state and
stays there until somebody presses the physical button on its base. No software fixes this,
and **a smart plug does not help** — restoring power just leaves the unit sitting there, off
and not advertising.

Everything else is unattended. Turning it on and off over Bluetooth both work with nobody
touching the device, and your settings survive the outage.

### The phone app's schedules stop firing

While this integration is installed, schedules set in the Kirri app never run.

Turning the diffuser off writes an always-matching schedule with a zero dispense time to the
device — that is what makes *off* reliable — and it outranks whatever the app put there. The
app's schedules are **masked, not erased**: remove the integration and they resume.

## Scheduling

Schedule the diffuser with ordinary Home Assistant automations. "Mornings 10:00–12:00,
Delicate" is two of them:

```yaml
automation:
  - alias: Kirri — mornings on
    triggers:
      - trigger: time
        at: "10:00:00"
    actions:
      - action: select.select_option
        target: { entity_id: select.kirri_beacon_intensity }
        data: { option: Delicate }
      - action: switch.turn_on
        target: { entity_id: switch.kirri_beacon }

  - alias: Kirri — mornings off
    triggers:
      - trigger: time
        at: "12:00:00"
    actions:
      - action: switch.turn_off
        target: { entity_id: switch.kirri_beacon }
```

Entity ids depend on the name you gave the device — check **Developer tools → States**. From
here you get everything Home Assistant offers and the device does not: sun events, presence,
template conditions, and a trace when it misbehaves.

## Options

**Settings → Devices & services → Kirri Beacon → Configure**

| | |
|---|---|
| **Status poll interval** | Default 900 s. A safety net only: every change is confirmed by the device as it happens, and availability comes from its advertisements. Long intervals cost nothing. |
| **Keep the connection open for** | Default 2 s. How long the Bluetooth link is held after a command, so a follow-up does not pay for another connect — each one costs 3–5 s. |

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Everything `unavailable` | The diffuser is not advertising. After a power cut, press the button on its base. |
| Setup: *not reachable* | The diffuser is off, or the proxy beside it is offline. |
| Setup: *not supported* | Something answered, but it is not a Beacon — see the `0000fff0-…` note above. |
| Commands fail while the network is busy | If the proxy is an ESPHome board, check it is on 2026.5.0 or newer. |

For anything else, turn on debug logging — every frame is logged as `TX …` / `RX …`, which
is usually the whole diagnosis:

```yaml
logger:
  logs:
    custom_components.kirri_beacon: debug
```

**Redact Bluetooth addresses before posting a log.**

## Extending it

| | |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | running the tests, and what cannot be checked without the hardware |
| [`docs/protocol.md`](docs/protocol.md) | the BLE protocol — frames, checksums, schedule records |
| [`docs/decisions.md`](docs/decisions.md) | the ADRs: why things are built the way they are |
| [`docs/hardware.md`](docs/hardware.md) | proxy boards, siting, and the signal levels to expect |

```sh
python3 tools/frames.py
```

Prints and self-checks every frame in the protocol spec. No dependencies, no hardware, bare
Python 3 — the fastest way to sanity-check a change to the codec.

## Licence and trademarks

MIT — see [`LICENSE`](LICENSE).

**Kirri**, **Kirri Beacon** and **Scent Australia Home** are the vendor's marks, not this
project's. They are used to say *which* diffuser this controls, and that is all the images in
`custom_components/kirri_beacon/brand/` do as well. Nothing here is affiliated with, endorsed
by, or produced by Scent Australia Home.

This is an independent interoperability project, built from what the device and its own app
can be observed doing. No vendor code or extracted vendor material is redistributed here.
