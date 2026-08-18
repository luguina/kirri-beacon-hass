# Kirri proxy — ESPHome firmware

The BLE→WiFi bridge that lets Home Assistant reach the diffuser three floors away.

**This board contains no Kirri protocol logic.** It is a stock, transparent
`bluetooth_proxy`; all framing lives in the HA integration
([ADR-001](../docs/decisions.md), [ADR-007](../docs/decisions.md)). That is deliberate —
if the protocol understanding changes, the fix is a software update in HA, not a trip
upstairs with a USB cable.

| | |
|---|---|
| Board | DFRobot FireBeetle 2 ESP32-E (`dfrobot_firebeetle2_esp32e`) |
| Config | [`kirri-proxy.yaml`](kirri-proxy.yaml) |
| Validated against | ESPHome **2026.7.4** — `esphome config` clean, no warnings |
| Minimum ESPHome | **2026.5.0** (see below) |

---

## Setup

**Every command in this file is written to run from the repository root**, so paths are
unambiguous and match the flash commands below.

```bash
cp esphome/secrets.yaml.example esphome/secrets.yaml
$EDITOR esphome/secrets.yaml
```

**The file must live in `esphome/`, next to the config.** ESPHome resolves `!secret` from
`<directory of the config file>/secrets.yaml` (`yaml_util.py:606`) — never from your shell's
working directory. A `secrets.yaml` in the repo root is simply not read, and the build fails
with a missing-secrets error rather than quietly using the wrong file.

`secrets.yaml` is gitignored (twice — repo root and `esphome/.gitignore`). Generate the API
key with:

```bash
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

You can leave `kirri_mac` as the placeholder for the first flash — everything except the
*Kirri RSSI* sensor works without it, and the first boot log is where you'll read the real
address from anyway (see [Verify](#verify)).

## Flash

First flash must be over USB; everything after that is OTA.

### Option A — from this machine

```bash
python3 -m venv ~/.venvs/esphome
~/.venvs/esphome/bin/pip install esphome
~/.venvs/esphome/bin/esphome run esphome/kirri-proxy.yaml
```

Run it from the repo root.

**If no serial port appears** (`ls /dev/cu.*` shows nothing new), don't start debugging
ESPHome — the problem is below it. Check in this order:

1. **A browser tab has already claimed the board.** This is the likeliest cause if you've
   had an ESPHome web installer or dashboard *"Plug into this computer"* page open, because
   Web Serial takes the device *exclusively* — macOS then never publishes the USB
   interface, the kernel driver never binds, and no `/dev/cu.*` node is ever created. Close
   the tab. Confirm with:
   ```bash
   ioreg -w0 -l -r -n "USB Serial" | grep IOUserClientCreator
   ```
   Any process named there is holding the port. Empty output means nothing has it.
2. **Charge-only USB-C cable.** Check whether the board enumerated at all:
   ```bash
   ioreg -p IOUSB -l -w0 | grep -E '"USB Product Name"|idVendor|idProduct'
   ```
   Nothing new in that list = the cable has no data lines, or the board isn't powered.
3. **macOS has no driver that matches this exact chip.** This is what actually happened
   here, and it is not obvious, because Apple's CH34x driver *is* installed — it just
   doesn't claim this device.

   The board carries a **CH340K**: `idVendor` 6790 (`0x1A86`), `idProduct` 29986
   (**`0x7522`**). Apple's `AppleUSBCHCOM.dext` matches only two devices — check for
   yourself rather than trusting that the dext's presence means support:
   ```bash
   plutil -p /System/Library/DriverExtensions/com.apple.DriverKit-AppleUSBCHCOM.dext/Info.plist \
     | grep -E "idVendor|idProduct"
   #   idProduct 29987 (0x7523)  CH340
   #   idProduct 21972 (0x55D4)  CH9102
   ```
   `0x7522` is one PID off the supported `0x7523`, so nothing ever attaches. Fix:
   ```bash
   brew install --cask wch-ch34x-usb-serial-driver
   ```
   then approve the driver extension in **System Settings → General → Login Items &
   Extensions**, reboot, and replug. The port appears as `/dev/cu.wchusbserial*`.

   *(Verified on macOS 26.5.2 / arm64. Note DFRobot document the FireBeetle 2 ESP32-E with
   a CP210x — a CH340K means a board variant or clone. Harmless for a BLE proxy, which
   uses no GPIO, but worth knowing if you ever add a sensor to this board.)*

**What is NOT the problem:** the BOOT/RESET buttons. The USB-serial bridge and the ESP32
are separate chips — the bridge enumerates on USB whatever the ESP32 is doing. BOOT only
decides whether the *ESP32* enters download mode, which matters when esptool tries to sync,
i.e. long after a port exists. If there is no `/dev/cu.*` node, no button on the board can
create one.

Most FireBeetle 2 boards auto-reset into the bootloader. If the flash can't sync, hold
**BOOT** while esptool connects.

Validate without flashing:

```bash
~/.venvs/esphome/bin/esphome config esphome/kirri-proxy.yaml
```

### Option B — ESPHome Builder add-on in Home Assistant

Copy `kirri-proxy.yaml` and your `secrets.yaml` into the add-on's `esphome/` directory,
then **Install → Plug into this computer** — the dashboard flashes over Web Serial from
your browser, so the USB cable goes into your laptop even though the dashboard runs on the
HA host. Subsequent installs use **Wirelessly**.

> Don't use the generic [ESPHome web installer](https://esphome.io/projects/) Bluetooth
> Proxy image. It works, but it isn't this config — you'd lose the diagnostic sensors and
> the pinned `min_version`, and there'd be nothing in the repo describing what's actually
> running.

## Adopt in Home Assistant

The device announces itself over mDNS; **Settings → Devices & Services** should offer
`kirri-proxy` for the ESPHome integration. It'll ask for the encryption key from
`secrets.yaml`.

## Verify

Four things, in order. Each one rules out a distinct failure.

1. **The proxy is active, not passive.** In the ESPHome log on boot:
   ```
   [bluetooth_proxy] Proxy started with 3 connection slots
   ```
   Slots at all means `active: true` took effect. A passive proxy forwards advertisements
   only and **no GATT write will ever reach the diffuser** — HA will see the device and be
   unable to control it, with no error explaining why. This is the single most common way
   this setup silently fails.

2. **The proxy can see the Kirri.** Set `logger: level: DEBUG` temporarily and look for:
   ```
   [esp32_ble_tracker] Found device XX:XX:XX:XX:XX:XX RSSI=-XX  name='AJBLE100'
   ```
   Put that address in `secrets.yaml` as `kirri_mac`, re-flash, and the *Kirri RSSI* sensor
   comes alive. Then put `logger` back to `INFO` — verbose BLE logging on a proxy is a real
   throughput cost.

3. **Home Assistant sees it through the proxy.** **Settings → Devices & Services →
   Bluetooth → Configure** should list the Kirri with the proxy as its source. If HA's own
   adapter is what's listed, you're not testing what you think you are.

4. **Record the numbers.** Both RSSI figures go in your own install-site table — the bands
   to read them against are in [`../docs/hardware.md`](../docs/hardware.md), and this build's
   are in [`../docs/lab/soak-2026-08.md`](../docs/lab/soak-2026-08.md). That's issue #5. Do
   this *before* anything
   stops working, because a radio problem on this project is indistinguishable from a
   protocol bug until you have a baseline to compare against.

## Two settings that are load-bearing

**`bluetooth_proxy: active: true`** — covered above. Without it, nothing works and nothing
says so.

**`min_version: 2026.5.0`** — 2026.5.0 carries a BLE reliability fix that holds
`ESP_COEX_PREFER_BT` for the lifetime of an active connection. Below that version,
sustained WiFi traffic makes GATT connects fail with `status=0x85` — the classic "error
133". That failure mode is intermittent and looks exactly like a bad protocol
implementation, which is a genuinely expensive thing to debug on a project whose protocol
was reverse-engineered.

## ⚠️ The proxy has ONE bluetooth subscriber slot

Worth knowing before you debug the proxy with a standalone script, because the symptom is
badly misleading.

`BluetoothProxy::subscribe_api_connection()` keeps a **single** `api_connection_`, and only
`SubscribeBluetoothLEAdvertisementsRequest` claims it. **Every** proxy response — including
GATT connect results — goes to whoever holds that slot, and the newest subscriber silently
displaces the previous one.

So a debug client that connects and issues a GATT connect *without* subscribing to
advertisements first gets its request executed correctly, has the **response delivered to
Home Assistant**, and times out. The proxy log shows a successful open followed by an
unexplained disconnect, which reads exactly like a broken device.

Two rules: **subscribe to raw advertisements first**, and **restart the node afterwards** —
your script displaced HA, and HA has no way to know it must re-subscribe. The `Restart`
button exists partly for this.

Full write-up in [`../docs/research/dossier.md` §7.3](../docs/research/dossier.md).

## Known upstream bug

[home-assistant/core#176516](https://github.com/home-assistant/core/issues/176516) —
proxy connection slots can leak, producing *"No backend with an available connection
slot"* and devices stuck unavailable. It does **not** self-heal; it needs a Bluetooth
integration reload. Open as of Jul 2026.

Mitigations already baked in: the integration connects on demand and disconnects after an
idle window rather than holding the link ([ADR-006](../docs/decisions.md)), and three
slots for one device means a single leaked slot isn't fatal.

## Diagnostics exposed

| Entity | Why it exists |
|---|---|
| Kirri RSSI | The BLE link to the diffuser — issue #5, and the baseline for later failures |
| WiFi Signal | The long link. This is the one that decides whether a smaller board would have worked |
| Connected BSSID | Which AP it associated with. In a multi-AP house, roaming to the wrong AP looks like a flaky proxy |
| Uptime / Status | Distinguishes "crashed and rebooted" from "network dropped" |
| Internal Temperature | Cheap; rules out thermal throttling near a heat source |
| Restart | Recovering the board without walking up three floors |
