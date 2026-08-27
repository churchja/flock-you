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

cd api && ../.venv/bin/python flockyou.py   # dashboard — run from api/, see below
.venv/bin/python -m pytest                  # tests — run from the repo root
```

**Launch the dashboard from `api/`, not the repo root.** `export_csv` builds its output
path relative to the current working directory, but Flask's `send_file` resolves it
against `app.root_path` (`api/`). The two agree only when the CWD is `api/`; launch from
anywhere else and every CSV/KML export writes successfully but fails to download.
`api/tests/test_export_paths.py` pins both halves of this.

## Tests

`pytest` covers the dashboard only — 30 tests over NMEA parsing, OUI lookup, and the
CSV export/import round-trip. The firmware has none: `main.cpp` is one Arduino
translation unit, so testing it needs either the detection predicates extracted behind a
natively-compilable seam or hardware-in-the-loop via `pio test`.

Five tests are `xfail(strict=True)` — they encode known bugs (the CSV round-trip drops
`ssid`, `channel` and `detection_count`, rewrites `protocol` to `bluetooth_le`; exports
break when the CWD isn't `api/`). Strict means fixing a bug turns its xfail into a
failing XPASS. That is the signal to promote it to a plain assertion, not to delete it.

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
