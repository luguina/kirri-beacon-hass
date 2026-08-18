# Contributing

Read this first, because one thing about this project is unusual and it decides what is
worth your time.

## Most changes here cannot be verified without the hardware

This integration talks to a **Kirri Beacon (SKU SAH5910)** over BLE, using a protocol
recovered by reverse-engineering the vendor's Android app. The test suite covers the frame
codec, the write-confirmation rules and the entity layer against fakes — it does not cover
whether a real diffuser accepts a frame, and nothing running in CI ever touches a device.

So if you do not have a Beacon, say so in the PR. **"Untested on hardware" is a useful and
welcome answer**; it tells the maintainer exactly what still needs checking. Guessing is
not, and a plausible-looking protocol change that has never met a device is the one
contribution that costs more to review than to write.

The one meaningful thing you *can* run with no hardware and no dependencies at all:

```sh
python3 tools/frames.py
```

It prints every frame in [`docs/protocol.md`](docs/protocol.md) with its checksums and
verifies the decoder against echoes captured from a real device. It runs on a bare Python 3.
If you are changing `protocol.py`, this is the check that matters most.

## Setting up

```sh
uv venv --python 3.14 ~/.venvs/ha
uv pip install --python ~/.venvs/ha/bin/python -r requirements_test.txt
~/.venvs/ha/bin/python -m pytest -q
```

**Python 3.14 is not optional.** Home Assistant 2026.8 requires it, and pip does not tell you
when it cannot have it — on 3.12 it silently backtracks to HA 2025.1.4 and on 3.13 to
2026.2.3. Both import cleanly and then test a core nobody runs. `uv` downloads a standalone
3.14 by itself, so this needs no system Python and no `sudo`.

## `python3 -m pytest` is not the full suite

This is the trap, and it is worth knowing before it costs you a review round:

| what you run | what you get |
|---|---|
| a bare `python3 -m pytest` | `N passed, **2 skipped**` |
| the venv above | `N passed`, no skips |

`tests/test_device.py` and `tests/test_entities.py` both open with
`pytest.importorskip("homeassistant")`, and `tests/test_soak.py` does the same for PyYAML.
Without those installed, each of those modules skips **as a whole** — dozens of test
functions never run — and **pytest still exits 0**.

So read the skip count, not the pass count. **Any "2 skipped" is the signature of an
incomplete run**, and the two modules it hides are the entire BLE transport and entity layer.
CI enforces this: the `Tests` workflow fails the build on any skip at all, rather than
checking a pass total that goes stale every time someone adds a test.

## Before you open a PR

```sh
ruff check .                              # config lives in pyproject.toml
~/.venvs/ha/bin/python -m pytest -q       # must report 0 skipped
python3 tools/frames.py                   # if you touched the codec
```

The ruff ruleset is deliberately small — real defects and honest suppressions, not
reformatting. `RUF100` is on, so a `# noqa` for a rule that no longer fires is itself an
error; if you add a suppression, it has to be one ruff would actually emit.

## Decisions are recorded, not re-argued

[`docs/decisions.md`](docs/decisions.md) holds numbered ADRs, and a few of them exist
specifically to stop a settled question being reopened — why scheduling lives in Home
Assistant rather than in the device's own schedule records (ADR-009), why "off" is a record
with zero dispense time rather than a cleared record (ADR-011), why a missing echo is retried
and a mismatched one is not (ADR-013).

If you are changing something an ADR covers, **say which one and why it should change**. That
is a perfectly good PR. Changing it without noticing the ADR is what wastes the round trip.

New decisions of that weight get a new ADR in the same file, in the same shape as the
existing ones.

## Reporting a bug

Set `custom_components.kirri_beacon` to debug and reproduce it — that logs every frame as
`TX …` / `RX …`, which is usually the whole diagnosis. **Redact your MAC addresses**; the
placeholder used throughout this repository is `11:22:33:44:55:66`, and
[`docs/privacy.md`](docs/privacy.md) has the command that checks a tree for real ones.
