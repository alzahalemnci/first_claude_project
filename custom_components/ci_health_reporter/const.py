"""
const.py — Constants for the CI Health Reporter integration
============================================================

WHY A SEPARATE FILE FOR CONSTANTS?
------------------------------------
In any project with more than one file, it's common to need the same value
in multiple places. If you write the raw string "ci_health_reporter" in five
different files, and you later need to rename it, you have to find and change
it in five places — and probably miss one.

By defining every reusable value here and importing from this file,
you have a single source of truth: change it once, it updates everywhere.

This is sometimes called the DRY principle — Don't Repeat Yourself.
"""

# ---------------------------------------------------------------------------
# DOMAIN
# ---------------------------------------------------------------------------
# The "domain" is Home Assistant's internal identifier for this integration.
# It must:
#   - Be all lowercase
#   - Use underscores, not hyphens or spaces
#   - Match the name of the folder inside custom_components/
#     (i.e. custom_components/ci_health_reporter/)
#   - Match the "domain" field in manifest.json
#
# HA uses the domain as:
#   - The key in configuration.yaml (ci_health_reporter: ...)
#   - The key in hass.data[DOMAIN] (where we store our integration's state)
#   - The prefix for any entities this integration creates (e.g. sensor.ci_health_reporter_xxx)
DOMAIN = "ci_health_reporter"


# ---------------------------------------------------------------------------
# CONFIGURATION KEYS
# ---------------------------------------------------------------------------
# These strings are the keys the user writes in their configuration.yaml.
# For example:
#
#   ci_health_reporter:
#     server_url: "http://192.168.1.189"   <-- CONF_SERVER_URL
#     server_port: 8765                     <-- CONF_SERVER_PORT
#     interval: 60                          <-- CONF_INTERVAL
#
# We store them as constants so that if you ever need to rename a config key,
# you change it in one place and update the README — you don't hunt through code.

CONF_SERVER_URL = "server_url"    # The base URL of the reporting server (scheme + host)
CONF_SERVER_PORT = "server_port"  # The port the server is listening on
CONF_INTERVAL = "interval"        # How often (in seconds) to send a report


# ---------------------------------------------------------------------------
# DEFAULT VALUES
# ---------------------------------------------------------------------------
# These are the fallback values used when the user doesn't specify a config
# option. HA's voluptuous schema system (in __init__.py) uses these defaults
# so the user only has to provide what they actually want to change.

DEFAULT_INTERVAL = 60
# 60 seconds = 1 minute. Good for testing so you can see reports quickly.
# In production you might raise this to 300 (5 min) to reduce CPU load on
# a Raspberry Pi. The minimum enforced by the schema is 10 seconds.

DEFAULT_SERVER_PORT = 8765
# The port our mock server (and any real server) should listen on.
# 8765 is an arbitrary high-numbered port — it's unlikely to conflict
# with other services. Common alternatives: 8080, 8000, 9000.

DEFAULT_LOW_BATTERY_THRESHOLD = 20
# Batteries at or below this percentage are flagged as "low" in the payload.
# 20% is a reasonable threshold — low enough to still have time to replace,
# high enough to give early warning.


# ---------------------------------------------------------------------------
# HTTP SETTINGS
# ---------------------------------------------------------------------------

HEALTH_ENDPOINT_PATH = "/health"
# The URL path we POST to on the server. Combined with server_url and
# server_port it forms the full URL:
#   http://192.168.1.189:8765/health
#
# In REST conventions, /health is a common path for health/status endpoints.

HTTP_TIMEOUT_SECONDS = 10
# How long (in seconds) we wait for the server to respond before giving up.
# If the server is slow or unreachable, we don't want the integration to hang
# indefinitely — a 10-second timeout is generous for a LAN request.
# aiohttp.ClientTimeout uses this value in coordinator.py.
