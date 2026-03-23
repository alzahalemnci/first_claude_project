# CI Health Reporter

A Home Assistant custom integration that periodically gathers health data from your HA instance — battery levels, offline entities, and automation states — and ships it as JSON to an HTTP server on your network.

Built as a proof of concept for monitoring Home Assistant from an external service (e.g. a dashboard, alerting system, or data store running on another machine).

---

## What It Reports

Every report is a JSON payload sent via HTTP POST to your server. It contains:

| Field | Description |
|---|---|
| `batteries` | All entities with a battery level, including a `low` flag for anything at or below 20% |
| `offline_entities` | Every entity whose state is `unavailable` or `unknown` |
| `automations` | All automations — enabled/disabled state and when each last triggered |
| `summary` | Counts for quick at-a-glance stats |
| `timestamp` | UTC ISO 8601 timestamp of when the report was generated |
| `ha_version` | Your Home Assistant version |

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
    "automations_disabled": 1
  }
}
```

---

## Requirements

- Home Assistant (any recent version — tested concept against 2024.x)
- Python 3.11+ (bundled with Home Assistant)
- Network access between the HA host and your server

---

## Installation

### Step 1 — Copy the integration

Copy the `custom_components/ci_health_reporter` folder into your Home Assistant configuration directory:

```
<your HA config dir>/
└── custom_components/
    └── ci_health_reporter/
        ├── __init__.py
        ├── manifest.json
        ├── const.py
        └── coordinator.py
```

Your HA config directory is typically `/config` (if running in Docker or Home Assistant OS) or `~/.homeassistant` (if running as a standalone Python install).

**Using SCP (from your computer to the HA host):**
```bash
scp -r custom_components/ci_health_reporter homeassistant@<HA_IP>:/config/custom_components/
```

**Using the HA file editor add-on (Home Assistant OS):**

If you have the File Editor or Studio Code Server add-on installed, you can upload or create the files directly through the HA web UI.

### Step 2 — Configure `configuration.yaml`

Add the following block to your `configuration.yaml`:

```yaml
ci_health_reporter:
  server_url: "http://192.168.1.189"   # IP of your reporting server
  server_port: 8765                     # port your server is listening on
  interval: 60                          # seconds between reports (minimum: 10)
```

All fields except `server_url` are optional. Defaults:

| Key | Default | Description |
|---|---|---|
| `server_url` | *(required)* | Full URL including scheme, e.g. `http://192.168.1.189` |
| `server_port` | `8765` | Port the HTTP server is listening on |
| `interval` | `60` | Seconds between reports (min: 10) |

### Step 3 — Restart Home Assistant

After saving `configuration.yaml`, restart HA:

- **Home Assistant OS / Supervised:** Settings → System → Restart
- **Docker:** `docker restart homeassistant`
- **CLI:** `ha core restart`

### Step 4 — Check the logs

Verify the integration started correctly by checking your HA logs:

- **UI:** Settings → System → Logs → search for `ci_health_reporter`
- **File:** `<config dir>/home-assistant.log`

You should see:
```
INFO (MainThread) [custom_components.ci_health_reporter] CI Health Reporter: starting — reporting to http://192.168.1.189:8765 every 60s
```

---

## Running the Mock Server

The `mock_server/` directory contains a simple Python server for testing. It listens for POST requests and pretty-prints the received payload to the terminal.

### Start the server

On the machine at `192.168.1.189`:

```bash
python mock_server/server.py 8765
```

You should see:
```
Mock health server listening on 0.0.0.0:8765/health
Press Ctrl+C to stop.
```

When HA sends a report, the payload is printed to the terminal:
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

To keep the mock server running as a background service:

```bash
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

## Adjusting the Report Interval

The default interval is **60 seconds** — suitable for testing and light monitoring. For production use on a Raspberry Pi, you may want to increase this to reduce CPU load.

To change it, update `configuration.yaml`:

```yaml
ci_health_reporter:
  server_url: "http://192.168.1.189"
  server_port: 8765
  interval: 300    # 5 minutes
```

Then restart HA.

---

## Project Structure

```
ci_health_reporter/
├── custom_components/
│   └── ci_health_reporter/
│       ├── __init__.py        # Integration entry point and scheduling
│       ├── coordinator.py     # Data gathering and HTTP POST logic
│       ├── const.py           # Constants and defaults
│       └── manifest.json      # HA integration metadata
├── mock_server/
│   └── server.py              # Test server (BaseHTTPRequestHandler)
└── README.md
```

---

## Troubleshooting

**The integration doesn't appear to be loading**

Check that the folder is named exactly `ci_health_reporter` and placed inside a `custom_components` directory at the root of your HA config directory.

**No data is being received by the server**

1. Confirm the server is running: `curl -X POST http://192.168.1.189:8765/health -H "Content-Type: application/json" -d '{}'`
2. Check HA logs for errors from `custom_components.ci_health_reporter`
3. Make sure the HA host can reach `192.168.1.189` over the network (ping it from the HA terminal)

**`Invalid config` error on startup**

The `server_url` must include the scheme (`http://` or `https://`). Make sure your `configuration.yaml` entry is quoted and starts with `http://`.

**Batteries not showing up**

The integration detects battery entities two ways:
- Sensors with `device_class: battery`
- Any entity with a `battery_level` attribute

If your battery sensors use a different attribute name, check the entity's attributes in Developer Tools → States and open an issue with the attribute name.

---

## License

MIT
