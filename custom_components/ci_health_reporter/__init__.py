"""
__init__.py — Integration entry point for CI Health Reporter
=============================================================

WHY IS THIS FILE CALLED __init__.py?
--------------------------------------
In Python, any folder that contains a file called __init__.py is treated as
a "package" — a collection of modules that can be imported together.

When Home Assistant loads a custom integration from the custom_components/
folder, the first file it looks for and runs is __init__.py. This file is
responsible for:
  1. Telling HA what configuration options are valid (CONFIG_SCHEMA)
  2. Reading those options from configuration.yaml
  3. Setting up the integration — in our case, creating the coordinator and
     scheduling the recurring health reports

HOW HA LOADS THIS FILE:
  When HA starts and reads configuration.yaml, it sees "ci_health_reporter:"
  and looks for a folder named ci_health_reporter inside custom_components/.
  It then imports this __init__.py and calls async_setup() automatically.

ASYNC vs SYNC in HOME ASSISTANT:
  Almost everything in HA is "async" (asynchronous). This means functions
  don't block — instead of waiting for one thing to finish before starting
  the next, HA can juggle many things at once.

  The key words you'll see:
    - async def   → defines a function that can pause and let other things run
    - await       → pauses this function until the awaited thing finishes
    - asyncio     → Python's built-in async system that HA is built on

  This matters for us because our HTTP POST (coordinator.py) is a network
  operation. Using async means HA doesn't freeze while waiting for the server.

Configuration (configuration.yaml):

    ci_health_reporter:
      server_url: "http://192.168.1.189"
      server_port: 8765
      interval: 60        # seconds between reports (default: 60)
"""

import logging                          # Python's built-in logging system
from datetime import timedelta          # Used to express "60 seconds" as a duration object

import voluptuous as vol                # Third-party schema validation library (bundled with HA)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP  # HA event fired on shutdown
from homeassistant.core import HomeAssistant              # The main HA object — contains everything
from homeassistant.helpers import config_validation as cv # HA's pre-built voluptuous validators
from homeassistant.helpers.event import async_track_time_interval  # Schedules repeating callbacks
from homeassistant.helpers.typing import ConfigType       # Type hint: the full parsed config dict

# Relative imports — the dot (.) means "from this same package folder"
from .const import (
    CONF_INTERVAL,
    CONF_SERVER_PORT,
    CONF_SERVER_URL,
    DEFAULT_INTERVAL,
    DEFAULT_SERVER_PORT,
    DOMAIN,
)
from .coordinator import HealthReporterCoordinator


# ---------------------------------------------------------------------------
# LOGGER
# ---------------------------------------------------------------------------
# __name__ resolves to the full module path, e.g.:
#   custom_components.ci_health_reporter
#
# HA uses this name to route log messages. You can filter logs by this name
# in the HA UI under Settings → System → Logs, or in home-assistant.log.
#
# Usage:
#   _LOGGER.debug("...")    → only shown when HA log level is DEBUG
#   _LOGGER.info("...")     → shown at INFO level and above
#   _LOGGER.warning("...")  → shown at WARNING level and above
#   _LOGGER.error("...")    → always shown
_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONFIG_SCHEMA
# ---------------------------------------------------------------------------
# This tells HA exactly what our section in configuration.yaml is allowed to
# contain, what types each field must be, and what the defaults are.
#
# Home Assistant uses the "voluptuous" library for schema validation.
# When HA parses configuration.yaml, it automatically runs CONFIG_SCHEMA
# against our block. If the user provides invalid values (e.g. a negative
# interval), HA shows a config error and refuses to load the integration —
# which is much safer than letting bad values reach our code.
#
# SCHEMA STRUCTURE:
#   vol.Schema({ ... })         → defines a dict with specific keys
#   vol.Required(key)           → this key MUST be present in configuration.yaml
#   vol.Optional(key, default)  → this key is optional; use `default` if missing
#   cv.url                      → validates the value is a valid URL (has http:// etc.)
#   cv.port                     → validates the value is a valid port number (1–65535)
#   cv.positive_int             → validates the value is an integer > 0
#   vol.Range(min=10)           → validates the value is at least 10
#   vol.All(a, b)               → runs validator `a` then validator `b` (both must pass)
#
# extra=vol.ALLOW_EXTRA means if the user adds unrecognised keys under
# ci_health_reporter:, HA won't reject the whole config — it just ignores them.
# This is important at the top level because other integrations also live in
# the same configuration.yaml dict.
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_SERVER_URL): cv.url,
                vol.Optional(CONF_SERVER_PORT, default=DEFAULT_SERVER_PORT): cv.port,
                vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): vol.All(
                    cv.positive_int, vol.Range(min=10)
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


# ---------------------------------------------------------------------------
# async_setup
# ---------------------------------------------------------------------------
# This is the function HA calls to start our integration. The name must be
# exactly "async_setup" — HA looks for this specific name.
#
# Parameters:
#   hass   → the HomeAssistant instance. This is the "god object" that holds
#             everything: entity states, event bus, scheduler, config, etc.
#             We pass it to our coordinator so it can query states and make
#             HTTP requests through HA's session.
#
#   config → the entire parsed configuration.yaml as a Python dictionary.
#             Our section is at config["ci_health_reporter"] (or config[DOMAIN]).
#
# Return value:
#   True  → setup succeeded, HA should consider this integration loaded
#   False → setup failed, HA will log an error and skip this integration
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """
    Set up the CI Health Reporter integration.

    Called by HA when it processes configuration.yaml.
    Reads our config block, creates the coordinator, and schedules
    the recurring health reports.
    """

    # config.get(DOMAIN) returns our config block, or None if the user
    # added the integration to custom_components/ but forgot to add the
    # ci_health_reporter: block in configuration.yaml.
    conf = config.get(DOMAIN)
    if conf is None:
        # This is not an error — the integration is installed but unconfigured.
        # Returning True tells HA "I loaded fine, I just have nothing to do."
        return True

    # Extract our three config values. Because we defined defaults in CONFIG_SCHEMA
    # with vol.Optional(..., default=...), these keys are always present in `conf`
    # even if the user didn't write them in configuration.yaml.
    server_url: str = conf[CONF_SERVER_URL]
    server_port: int = conf[CONF_SERVER_PORT]
    interval: int = conf[CONF_INTERVAL]

    # Log at INFO so the user can confirm the integration started correctly.
    # The %s placeholders are filled in by the logging system — this is faster
    # than f-strings because the string is only formatted if the message will
    # actually be displayed.
    _LOGGER.info(
        "CI Health Reporter: starting — reporting to %s:%s every %ss",
        server_url,
        server_port,
        interval,
    )

    # Create our coordinator. It holds all the data-gathering and HTTP logic.
    # See coordinator.py for details.
    coordinator = HealthReporterCoordinator(hass, server_url, server_port)

    # hass.data is a dict that integrations use to store their runtime state.
    # Using DOMAIN as the key prevents collisions with other integrations.
    # This allows other parts of the integration (if we add sensors later)
    # to find the coordinator via hass.data[DOMAIN].
    hass.data[DOMAIN] = coordinator

    # -----------------------------------------------------------------------
    # INITIAL REPORT — send one report as soon as HA finishes starting up
    # -----------------------------------------------------------------------
    # We don't call coordinator.async_update() directly here because async_setup
    # runs early in HA's startup sequence — some integrations and entities may
    # not be loaded yet. By listening for "homeassistant_start", we wait until
    # HA has fully initialised before sending the first report.
    #
    # hass.bus is the HA event bus — a publish/subscribe system where components
    # can fire named events and other components can listen for them.
    #
    # async_listen_once registers a one-time listener: it fires once, then
    # automatically removes itself. No cleanup needed.
    async def _send_initial_report(event=None):
        # `event` is the event object passed by the bus. We don't need it here,
        # so we accept it but ignore it (that's what `=None` default signals).
        await coordinator.async_update()

    hass.bus.async_listen_once("homeassistant_start", _send_initial_report)

    # -----------------------------------------------------------------------
    # RECURRING REPORTS — fire every `interval` seconds
    # -----------------------------------------------------------------------
    # async_track_time_interval registers coordinator.async_update as a callback
    # that HA will call on a fixed schedule.
    #
    # timedelta(seconds=interval) creates a Python duration object. timedelta
    # understands days, hours, minutes, seconds — here we just use seconds.
    #
    # The return value is a CANCEL function. Calling cancel_interval() removes
    # the listener from HA's scheduler so it stops firing. We use this on shutdown.
    cancel_interval = async_track_time_interval(
        hass,
        coordinator.async_update,  # The function to call on each tick
        timedelta(seconds=interval),
    )

    # -----------------------------------------------------------------------
    # SHUTDOWN CLEANUP
    # -----------------------------------------------------------------------
    # When HA stops, it fires the EVENT_HOMEASSISTANT_STOP event. We listen for
    # it so we can cancel our interval — this is clean, well-behaved code.
    # Without this, the interval might try to fire during shutdown and cause
    # errors or noisy log messages.
    async def _on_stop(event):
        cancel_interval()  # Tell HA to stop calling coordinator.async_update
        _LOGGER.info("CI Health Reporter: stopped")

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop)

    # Returning True tells HA our setup completed successfully.
    return True
