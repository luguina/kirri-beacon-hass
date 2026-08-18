# Captures

Session logs and decoded frame tables that serve as **evidence** for claims made in
[`../protocol.md`](../protocol.md) and [`../research/dossier.md`](../research/dossier.md).

## What lives here

| | |
|---|---|
| ✅ Committed | Text logs and decoded frame tables — **with device identifiers redacted** |
| ❌ Never committed | `*.btsnoop`, `*.pcap`, `*.pcapng`, `*.log`, `bugreport*`, `*.apk`, `*.zip` |

`.gitignore` enforces the second row. That exclusion is deliberate: an Android `bugreport`
bundle contains far more than an HCI log — WiFi SSIDs, account identifiers, installed packages,
system logs. **Do not `git add -f` one.**

## Redaction rule

**MAC addresses and other device identifiers are redacted in this directory.**

Redact on the way in, not later. A capture log is a bulk artefact — one careless paste scatters
an identifier across a hundred lines, and the diff that would have caught it is unreadable. The
rule that keeps this cheap is that no real address is ever committed anywhere in the tree, in a
capture or outside one: see [`../privacy.md`](../privacy.md) for what each placeholder here
stands for and the one command that checks nothing real has crept back in.

## Contents

| File | What it is |
|---|---|
| `2026-08-08-nrf-connect-gatt-session.txt` | nRF Connect session that characterised the device: full GATT discovery, CCCD behaviour, Generic Access reads, and the first (failed) Aroma-Link write attempts. MAC redacted. |

## Decoding worksheet

For a capture where the protocol is *not* already known, record what was done in the app
alongside what went over the air. The diff between two frames that differ by one UI action is
what identifies a field.

| # | App action | Direction | Handle | Bytes | Interpretation |
|---|---|---|---|---|---|
| 1 | | → | | | |

**Method that works:** change exactly one control at a time, capture, diff. Ten frames that
each vary one thing beat a hundred frames of mixed activity.

> For the Kirri Beacon this is no longer necessary — the protocol was recovered from the app's
> source maps rather than from traffic. Kept for any future device.
