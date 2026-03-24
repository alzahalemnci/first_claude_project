# CI Health Reporter

A Home Assistant custom integration that periodically gathers health data from your HA instance — battery levels, offline entities, and automation states — and makes it available in two ways:

1. **Live HA dashboard** — a built-in Lovelace "Service Overview" sidebar panel with KPI cards, active issues, health history graph, battery levels chart, and maintenance suggestions
2. **HTTP push** — JSON payloads posted to an HTTP server on your network for external dashboards, alerting, or data storage

![Deployed dashboard](ha_service_dashboard_deployed.jpg)

---

## What It Monitors

| Category | Details |
|---|---|
| **Battery levels** | All entities with a battery sensor or `battery_level` attribute. Flags anything at or below 20% as low. |
| **Offline entities** | Every entity whose state is `unavailable` or `unknown` |
| **Automations** | All automations — enabled/disabled state and last triggered time |
| **System health score** | A 0–100% score computed from the above (see formula below) |

### Health Score Formula

```
score = 100
      - min(low_battery_count  × 5,  30)   # max 30 pts
      - min(offline_count      × 3,  30)   # max 30 pts
      - min(disabled_count     × 2,  20)   # max 20 pts
clamped to [0, 100]
```

---

## HA Sensor Entities

The integration creates four sensor entities visible anywhere in HA (automations, alerts, other dashboards):

| Entity ID | Description |
|---|---|
| `sensor.ci_health_low_battery_count` | Count of low-battery devices |
| `sensor.ci_health_offline_count` | Count of offline/unknown entities |
| `sensor.ci_health_disabled_automations` | Count of disabled automations |
| `sensor.ci_health_system_health` | Overall health score (0–100 %) |

Each sensor also exposes full data as attributes (device lists, names, etc.) which the Lovelace dashboard reads via Jinja2 templates.

---

## HTTP Payload

Every report interval a JSON payload is also POSTed to your configured server.

### Example Payload

```json
{
  "timestamp": "2026-03-22T14:35:00+00:00",
  "ha_version": "2024.3.0",
  "batteries": [
    {
      "entity_id": "sensor.front_door_lock_battery",
      "friendly_name": "Front Door Lock Battery",
      "level": 12.0,
      "unit": "%",
      "low": true
    }
  ],
  "offline_entities": [
    {
      "entity_id": "sensor.outdoor_temperature",
      "friendly_name": "Outdoor Temperature",
      "state": "unavailable",
      "domain": "sensor",
      "last_updated": "2026-03-22T13:10:00+00:00"
    }
  ],
  "automations": [
    {
      "entity_id": "automation.morning_lights",
      "friendly_name": "Morning Lights",
      "enabled": true,
      "last_triggered": "2026-03-22T07:00:00+00:00"
    }
  ],
  "summary": {
    "battery_count": 5,
    "low_battery_count": 1,
    "low_battery_entities": ["sensor.front_door_lock_battery"],
    "offline_count": 1,
    "automation_count": 8,
    "automations_enabled": 7,
    "automations_disabled": 1,
    "system_health": 85
  }
}
```

---

## Requirements

- Home Assistant (any recent version — tested against 2024.x)
- Python 3.11+ (bundled with Home Assistant)
- Network access between the HA host and your reporting server (for HTTP push)

---

## Installation

### Step 1 — Copy the integration files

Copy the `custom_components/ci_health_reporter` folder into your HA config directory:

```
<your HA config dir>/
├── configuration.yaml
├── ci_health_dashboard.yaml      ← also copy this here (Step 3)
└── custom_components/
    └── ci_health_reporter/
        ├── __init__.py
        ├── coordinator.py
        ├── const.py
        ├── sensor.py
        └── manifest.json
```

Your HA config directory is typically `/config` (Home Assistant OS / Docker) or `~/.homeassistant` (standalone Python install).

**Using SCP:**
```bash
scp -r custom_components/ci_health_reporter homeassistant@<HA_IP>:/config/custom_components/
scp ci_health_dashboard.yaml homeassistant@<HA_IP>:/config/
```

**Using the HA File Editor add-on:** upload or create files directly through the HA web UI.

### Step 2 — Configure `configuration.yaml`

Add the following block:

```yaml
ci_health_reporter:
  server_url: "http://192.168.1.189"   # IP of your reporting server
  server_port: 8765                     # port your server is listening on
  interval: 60                          # seconds between reports (minimum: 10)
```

All fields except `server_url` are optional:

| Key | Default | Description |
|---|---|---|
| `server_url` | *(required)* | Full URL including scheme, e.g. `http://192.168.1.189` |
| `server_port` | `8765` | Port the HTTP server is listening on |
| `interval` | `60` | Seconds between reports (min: 10) |

### Step 3 — Set up the Lovelace dashboard

Copy `ci_health_dashboard.yaml` to your HA config root (same folder as `configuration.yaml`), then add this block to `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    ci-health:
      mode: yaml
      filename: ci_health_dashboard.yaml
      title: Service Overview
      icon: mdi:heart-pulse
      show_in_sidebar: true
```

### Step 4 — Restart Home Assistant

- **Home Assistant OS / Supervised:** Settings → System → Restart
- **Docker:** `docker restart homeassistant`
- **CLI:** `ha core restart`

A **Service Overview** item will appear in the left sidebar. Sensor entities populate within the first report interval (default 60 seconds).

### Step 5 — Check the logs

Verify the integration started correctly:

- **UI:** Settings → System → Logs → search `ci_health_reporter`
- **File:** `<config dir>/home-assistant.log`

You should see:
```
INFO (MainThread) [custom_components.ci_health_reporter] CI Health Reporter: starting — reporting to http://192.168.1.189:8765 every 60s
```

---

## Running the Mock Server

The `mock_server/` directory contains a simple Python server for testing. It listens for POST requests and pretty-prints the received payload to the terminal.

```bash
python mock_server/server.py 8765
```

Output:
```
Mock health server listening on 0.0.0.0:8765/health
Press Ctrl+C to stop.
```

When HA sends a report:
```
============================================================
Received health report at 2026-03-22T14:35:00+00:00
============================================================
{
  "timestamp": "2026-03-22T14:35:00+00:00",
  ...
}
============================================================
```

### Running on startup (Linux/systemd)

```ini
# /etc/systemd/system/ha-health-mock.service
[Unit]
Description=CI Health Reporter Mock Server

[Service]
ExecStart=/usr/bin/python3 /path/to/mock_server/server.py 8765
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable ha-health-mock
sudo systemctl start ha-health-mock
```

---

## Project Structure

```
ci_health_reporter/
├── custom_components/
│   └── ci_health_reporter/
│       ├── __init__.py        # Integration entry point and scheduling
│       ├── coordinator.py     # Data gathering, health score, HTTP POST
│       ├── const.py           # Constants, defaults, health penalty values
│       ├── sensor.py          # HA sensor entities (push-based, no polling)
│       └── manifest.json      # HA integration metadata
├── mock_server/
│   └── server.py              # Test server (BaseHTTPRequestHandler)
├── ci_health_dashboard.yaml   # Lovelace dashboard (copy to HA config root)
├── ha_service_dashboard.jpg   # Dashboard mockup
├── ha_service_dashboard_deployed.jpg  # Deployed screenshot
└── README.md
```

---

## Troubleshooting

**Integration doesn't load / no sidebar item**

- Ensure the folder is named exactly `ci_health_reporter` inside `custom_components/`
- Check HA logs for errors: Settings → System → Logs → search `ci_health_reporter`
- Make sure `ci_health_dashboard.yaml` is in the config root, not inside `custom_components/`

**Dashboard shows "Unknown" on all cards**

The sensors haven't received their first data yet. Wait up to one report interval (default 60s) after HA starts. If it persists, check the logs for HTTP errors.

**No data received by the mock server**

1. Confirm the server is running: `curl -X POST http://192.168.1.189:8765/health -H "Content-Type: application/json" -d '{}'`
2. Check HA can reach the server: ping it from the HA terminal
3. Check logs for `ClientError` messages from `ci_health_reporter`

**`Invalid config` error on startup**

The `server_url` must include the scheme. Make sure it starts with `http://` or `https://` and is quoted in `configuration.yaml`.

**Batteries not showing up**

The integration detects batteries two ways:
- Sensors with `device_class: battery`
- Any entity with a `battery_level` attribute

Check your entity's attributes in Developer Tools → States to see which pattern applies.

---

## License

MIT
