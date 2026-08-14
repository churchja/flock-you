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

pip install -r api/requirements.txt
python api/flockyou.py         # dashboard
```

There is no test suite yet. `/tdd` and `/diagnosing-bugs` will need one stood up before
their red-green-refactor loops can run.

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
