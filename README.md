# WiFi Test Utility

Lightweight CLI for scanning WiFi networks, running speed tests (Ookla / iperf3),
saving results to a local SQLite database, and exporting CSVs.

## Installation

```bash
poetry install
```

## Usage

```bash
# Initialize default config (auto-detects wifi interface when possible)
sudo wifi-test config init

# Manage config values
sudo wifi-test config set DB_PATH /path/to/wifi.db
sudo wifi-test config get

# Scan networks (--save/--no-save overrides AUTO_SAVE config)
sudo wifi-test scan --save
sudo wifi-test scan -p MyPrefix --limit 10

# Run a speedtest on current connection
sudo wifi-test speedtest

# Auto-connect mode: scan, connect to each matching AP, test, restore original
sudo wifi-test speedtest --auto-connect -p <PREFIX>
sudo wifi-test speedtest --auto-connect --limit 5 --details
sudo wifi-test speedtest --udp    # use UDP mode for iperf3

# Export data to CSV (prompts if no -o)
sudo wifi-test export -o wifi_export.csv
```

## What it does

- Scans WiFi networks and displays a rich, colorized table.
- Runs speed tests using either Ookla or iperf3 (configurable).
- Auto-connect mode: scans matching SSIDs, connects to each AP by BSSID, runs tests, and restores the original network.
- Persists results to a unified SQLite table (`network_results`) keyed by BSSID.
- Exports data to CSV with `bssid` as the primary lookup key.

## Notable behavior and validations

- Entry point: `wifi-test` (console script mapped to `wifi_test.cli:cli`).
- `SPEEDTEST_TOOL` supports `speedtest` (aliases: `ookla`) and `iperf3` (aliases: `iperf`) only; invalid values are rejected.
- Boolean config keys (`SCAN_FLUSH`, `AUTO_SAVE`) accept `true|false`, `1|0`, `yes|no` (case-insensitive) and are normalized.
- `IPERF3_PORT_RANGE` must be `start-end` with ports in 1–65535 and start ≤ end.
- `OUTPUT_DIR` and the parent directory of `DB_PATH` are created if missing and must be writable; the DB file itself is created lazily on first write.
- `network_results` uses `bssid` as a UNIQUE key; upserting keeps the latest row per device (no duplicate device rows).
- All timestamps use UTC via SQLite's `CURRENT_TIMESTAMP` stored in the `created_at` column.
- Exported CSV files are chowned to the original user when running via `sudo`.

## Configuration Options

| Key | Description | Default |
|-----|-------------|---------|
| `DB_PATH` | Path to SQLite database | `wifi_data.db` |
| `WIFI_INTERFACE` | WiFi interface name or `auto` for auto-detection | `auto` |
| `SPEEDTEST_TOOL` | Tool for speed tests: `speedtest` or `iperf3` | `speedtest` |
| `IPERF3_SERVER` | iperf3 server address (required for iperf3) | — |
| `IPERF3_PORT_RANGE` | iperf3 port range as `start-end` (e.g., `5201-5210`) | `5201-5201` |
| `IPERF3_BANDWIDTH` | Bandwidth for iperf3 `-b` flag (e.g., `100M`, `1G`) | `100M` |
| `SCAN_FLUSH` | Flush scan cache before scanning | `True` |
| `OUTPUT_DIR` | Directory for CSV exports | `.` |
| `AUTO_SAVE` | Automatically save scan/speedtest results to database | `True` |
| `PREFIX` | SSID prefix filter for scanning | — |
| `GOLDEN_CONFIG_PASSWORD` | Password for auto-connect networks | — |

### Config aliases

The config system accepts common aliases:

- `DB_PATH`: `PATH`, `DBPATH`, `DB`
- `WIFI_INTERFACE`: `INTERFACE`, `WIFI_IFACE`
- `SPEEDTEST_TOOL`: `SPEEDTEST`
- `GOLDEN_CONFIG_PASSWORD`: `GOLDEN_PASSWORD`, `GOLDEN_PASS`

### Clearing PREFIX

To clear any configured prefix and test all scanned networks with `--auto-connect`:

```bash
sudo wifi-test config set PREFIX ""
```

## CLI Dependencies

The following system tools are required for full functionality:

| Tool | Purpose |
|------|---------|
| `iw` | WiFi scanning and interface operations |
| `nmcli` | Network connection management |
| `speedtest` | Ookla Speedtest CLI (when `SPEEDTEST_TOOL=speedtest`) |
| `iperf3` | iperf3 tests (when `SPEEDTEST_TOOL=iperf3`) |

On Debian/Ubuntu:

```bash
sudo apt update && sudo apt install -y iw network-manager iperf3
# Ookla speedtest CLI install (official instructions):
# https://www.speedtest.net/apps/cli
```

## Tool Preview

![WiFi Scanner Screenshot](assets/scan_output.png)

### 📺 Walkthrough Demo (2 mins)

https://github.com/user-attachments/assets/464a24d6-8422-48f1-9e11-1c44728f1781

## Permissions

This tool requires root privileges for network operations (`iw`, `nmcli`). The CLI checks and errors if not run with `sudo`.

## Extensibility

The config system supports aliases and canonical names; more validations or aliases can be added centrally in `wifi_test/config.py`.
