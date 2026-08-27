# flock-you

Passive 2.4 GHz promiscuous-mode detector for Flock Safety surveillance infrastructure.
Two deliverables in one repo:

- **Firmware** — `main.cpp`, a single Arduino/PlatformIO translation unit targeting the
  Seeed XIAO ESP32-S3. Sniffs 802.11 management/data frames, matches Flock OUIs, persists
  to SPIFFS, and emits one JSON line per detection over USB CDC. Never transmits.
- **Dashboard** — `api/flockyou.py`, a Flask + Socket.IO app that ingests those JSON lines
  over serial, tags them with GPS from a USB NMEA dongle, and serves a live map.

`oui.txt` (root and `api/`) is the IEEE OUI database. `datasets/` holds the upstream
research this detection is built on — see `README.md` for full credit.

## Commands

```bash
pio run -e xiao_esp32s3        # build firmware
pio run -t upload              # flash
pio device monitor             # serial console (115200)

python -m venv .venv
.venv/bin/pip install -r api/requirements-dev.txt

.venv/bin/python api/flockyou.py   # dashboard — any working directory
.venv/bin/python -m pytest         # tests — run from the repo root
```

Exports are written under `app.root_path` (`api/exports/`) rather than the working
directory, so the dashboard can be launched from anywhere. `data/` is still resolved
relative to the CWD, so run the app from a consistent place if you want one cumulative
store — see `api/tests/test_export_paths.py`.

## Tests

`pytest` covers the dashboard only — 33 tests over NMEA parsing, OUI lookup, the CSV
export/import round-trip, and export path resolution. The firmware has none: `main.cpp`
is one Arduino translation unit, so testing it needs either the detection predicates
extracted behind a natively-compilable seam or hardware-in-the-loop via `pio test`.

Four of these tests began as `xfail(strict=True)` recording real defects in the CSV
round-trip; all four are fixed and the assertions now hold the line. If you add a test
for a bug you are not fixing yet, use `xfail(strict=True)` — fixing the bug then turns
the test into a failing XPASS, which is the prompt to promote it to a plain assertion
rather than let it rot.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `churchja/flock-you`, driven by the `gh` CLI (or the
`mcp__github__*` tools in remote sessions where `gh` is absent). See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name — `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `CONTEXT.md` + `docs/adr/` at the repo root, both created lazily by
`/domain-modeling` rather than upfront. See `docs/agents/domain.md`.
