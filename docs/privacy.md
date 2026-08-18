# Identifiers in this tree

Every address committed here is a **placeholder**. None of them identifies a device, a board or a
network belonging to this project. That is deliberately narrower than "these are not real
addresses": `192.168.1.50` is a live address on a great many home networks, and `10.0.0.5` on
plenty more. That is exactly what makes them safe to write down — an address that describes
thousands of networks points at none of them, and least of all at mine. What must never appear is
an address that is *ours*, in whole or in part.

This is a standing invariant, not a chore owed to some future publication — the public integration
repo is copied *from* this tree
([ADR-014](decisions.md#adr-014--the-public-repo-is-cut-fresh-this-history-stays-private)), so a
clean source tree is what makes the copy clean by construction.

Audited 2026-08-10 (#16), scrubbed 2026-08-17 (#42).

## What each placeholder stands for

Paths are relative to the repository root.

| Placeholder | Stands for | Where |
|---|---|---|
| `11:22:33:44:55:66` | the diffuser's BLE MAC | `docs/research/dossier.md` |
| `AA:BB:CC:DD:EE:F0` / `…:F2` | the FireBeetle's WiFi / BLE MAC | `docs/lab/soak-2026-08.md` |
| `AA:BB:CC:11:22:33` | the replacement XIAO's MAC | `docs/lab/soak-2026-08.md` |
| `192.168.1.50`–`.52` | the proxy's LAN addresses | `docs/lab/soak-2026-08.md` |
| `AA:BB:CC:DD:EE:FF` | the config-flow example address | `custom_components/kirri_beacon/strings.json` and its `translations/en.json` mirror, `tests/` |
| `10.0.0.5` | an example `--host` | `tools/kirri_probe.py` |
| `XX:XX:XX:XX:XX:XX` | "fill this in yourself" | `esphome/secrets.yaml.example`, `docs/captures/2026-08-08-nrf-connect-gatt-session.txt` |

The dossier's MAC is `11:…` rather than `AA:…` on purpose. §6.1 reasons about two bit-level
properties of the address's first octet, and `0xAA` has neither of them — so the repo's usual
placeholder would have left that paragraph arguing against its own digits. Redaction that
quietly falsifies the prose it sits in is its own kind of defect.

**Absent by decision, not by oversight:** no SSID or BSSID value appears anywhere in the tree (a
BSSID is geolocatable through public wardriving databases), and no APK, source map, capture
binary or `secrets.yaml` has ever been committed on any branch — verified against `git ls-files`,
not assumed from `.gitignore`. The SKU `SAH5910` stays: it is printed on the box and is the
single most useful search term for another owner finding this.

## The audit

One command, and the answer changes as the tree does — so re-run it rather than trusting this
page:

```sh
git ls-files -z | xargs -0 grep -nE \
  '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|([0-9A-Fa-f]{2}:){1,5}(…|\.\.\.)|(…|\.\.\.):[0-9A-Fa-f]{2}|\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))(\.[0-9]{1,3}){2}\b'
```

Four alternatives: a full MAC, a MAC truncated at the tail (`11:22:…`) or at the head (`…:66`),
and all three RFC 1918 ranges. The truncations are #42's lesson — the banner this page replaced
claimed **six** lines carried identifiers when the real number was **10**, and the audit it
published could not have found the difference, because it matched exactly six hex octets while
two of the leaks were **partial**: a real MAC cut off after its first two octets, ellipsis and
all. An audit that structurally cannot see what it is for is worse than no audit, since
re-running it keeps returning clean.

Note that the examples above are cut from the *placeholder*, `11:22:33:44:55:66`. #42 wrote its
own examples by truncating the **real** address, on the reasoning that two octets identify
nothing — and so left four copies of a real two-octet prefix in the file that announced the tree
was clean. Two octets is indeed close to worthless to an attacker. It is also not zero, and a
document whose subject is "no real octet is committed here" is the last place to make an
exception. They were removed with the banner; do not write another.

The `10.…` and `172.16–31.…` ranges are #16's lesson: an earlier version checked only
`192.168.…` and silently missed the `10.0.0.5` in `tools/kirri_probe.py`. It deliberately stops
short of matching a bare two-octet run like `19:31`, which would also match every timestamp in
the tree — over 300 lines of them. An audit nobody reads to the end fails the same way as one
that finds nothing.

That figure is deliberately not exact. The banner this page replaced hardcoded a count that
drifted, and a number in prose has no way to stay true; the point of the audit is that it is one
command whose answer is current.

It reports **candidates for a human to look at**, not confirmed leaks. Every hit should be a row
in the table above — **plus this page**, which quotes all of them and so matches itself. A hit
that is neither is the reason to run it. A false positive costs a glance; a false negative
publishes your network.
