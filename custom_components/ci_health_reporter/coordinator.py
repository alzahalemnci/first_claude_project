"""
coordinator.py — Data gathering and HTTP push logic
====================================================

This file contains the HealthReporterCoordinator class, which does the
actual work of this integration:

  1. Query the Home Assistant state machine for relevant entity data
  2. Assemble that data into a structured JSON-ready dictionary
  3. HTTP POST the dictionary to the configured reporting server

WHY A SEPARATE COORDINATOR FILE?
----------------------------------
We could put all of this logic in __init__.py, but separating concerns into
their own files makes the code easier to read, test, and extend. The pattern
of having a "coordinator" class that owns data-fetching is an official HA
convention (see homeassistant.helpers.update_coordinator.DataUpdateCoordinator).

We don't inherit from DataUpdateCoordinator here because that class is designed
for integrations that expose HA entities (sensors, binary sensors, etc.) which
subscribe to the coordinator's data. Since we're just pushing data out and
creating no HA entities, a plain class with async methods is simpler and
equally correct.

THE HOME ASSISTANT STATE MACHINE:
-----------------------------------
HA's state machine is like a giant dictionary that maps entity IDs to State
objects. Every device, sensor, automation, and virtual entity in HA has a
State object that contains:

  state.entity_id     → e.g. "sensor.living_room_temperature"
  state.state         → the current value as a string, e.g. "21.5" or "on" or "unavailable"
  state.attributes    → a dict of extra info, e.g. {"unit_of_measurement": "°C", "friendly_name": "Living Room Temp"}
  state.domain        → the part before the dot, e.g. "sensor"
  state.last_updated  → datetime when the state last changed value OR attributes
  state.last_changed  → datetime when the state value itself last changed

We access the state machine through hass.states.async_all(), which returns a
list of every State object currently tracked by HA.
"""

import logging
from datetime import datetime   # Used to check if last_triggered is a datetime object

# aiohttp is the async HTTP library bundled with Home Assistant.
# We use it to make our POST request without blocking the event loop.
import aiohttp

# HA provides string constants for common state values.
# Using these instead of raw strings like "unavailable" prevents typos.
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

# HomeAssistant is the main HA class — the "god object" with all subsystems.
from homeassistant.core import HomeAssistant

# async_get_clientsession returns HA's shared aiohttp session.
# IMPORTANT: Always use this instead of creating your own aiohttp.ClientSession.
# Reasons:
#   1. HA manages the session lifecycle (creates it, closes it on shutdown)
#   2. It includes SSRF protection middleware
#   3. It handles SSL context correctly
#   4. Creating your own session and forgetting to close it leaks resources
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# dt_util is HA's datetime utility module. It provides UTC-aware datetime
# objects. Always use dt_util.utcnow() instead of datetime.utcnow() in HA
# integrations because HA normalises everything to UTC-aware datetimes.
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_LOW_BATTERY_THRESHOLD,
    HEALTH_ENDPOINT_PATH,
    HTTP_TIMEOUT_SECONDS,
)


# Logger for this module. Messages appear in HA logs under
# "custom_components.ci_health_reporter.coordinator"
_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HA VERSION
# ---------------------------------------------------------------------------
# We include the HA version in each report so that whoever reads the data
# can correlate issues with HA upgrades.
#
# We wrap this in try/except because it's a convenience field — if for some
# reason the import fails, we don't want the entire integration to break.
try:
    import homeassistant as _ha_module
    HA_VERSION = _ha_module.__version__   # e.g. "2024.3.0"
except Exception:
    HA_VERSION = "unknown"


# ---------------------------------------------------------------------------
# HealthReporterCoordinator
# ---------------------------------------------------------------------------
class HealthReporterCoordinator:
    """
    Gathers HA health data and pushes it to a remote HTTP server.

    Lifecycle:
      - Created once in async_setup() in __init__.py
      - async_update() is called immediately after HA starts, then on
        every interval tick managed by async_track_time_interval
      - Lives for the lifetime of the HA process; no teardown needed
        beyond cancelling the interval listener (done in __init__.py)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        server_url: str,    # e.g. "http://192.168.1.189"
        server_port: int,   # e.g. 8765
        low_battery_threshold: int = DEFAULT_LOW_BATTERY_THRESHOLD,
    ) -> None:
        # Store the hass object so all methods can access the state machine,
        # the aiohttp session, and other HA subsystems.
        self.hass = hass

        # Build the full POST URL once here instead of rebuilding it on every
        # update. String concatenation is cheap but there's no need to repeat it.
        # Result example: "http://192.168.1.189:8765/health"
        self._url = f"{server_url}:{server_port}{HEALTH_ENDPOINT_PATH}"

        # Store the threshold so _gather_batteries() can use it without
        # needing it passed as an argument every call.
        self._low_battery_threshold = low_battery_threshold

    # ==========================================================================
    # PUBLIC ENTRY POINT
    # ==========================================================================

    async def async_update(self, now=None) -> None:
        """
        Gather health data and push it to the server.

        This is the method called by async_track_time_interval on each tick.
        The `now` parameter is a datetime object that HA passes automatically
        to time-interval callbacks — we don't need it here, but we must accept
        it or Python will raise a TypeError when HA tries to call us with it.

        async_update is an async method because _post_payload awaits an HTTP
        request. Any function that uses `await` must itself be `async def`.
        """
        _LOGGER.debug("CI Health Reporter: starting update")

        # Step 1: Build the payload dict (synchronous — just reads state machine)
        payload = self._build_payload()

        # Step 2: POST it to the server (async — does network I/O)
        await self._post_payload(payload)

    # ==========================================================================
    # PAYLOAD ASSEMBLY
    # ==========================================================================

    def _build_payload(self) -> dict:
        """
        Query the HA state machine and assemble the full health payload.

        This is a regular (non-async) method because all the HA state accessors
        we use (hass.states.async_all) are synchronous @callback methods —
        they don't do I/O, they just read in-memory data. So no `await` is needed.

        We call hass.states.async_all() ONCE and pass the result to the individual
        helper methods. This is important for performance: on a large HA instance
        there could be thousands of entities. Iterating once and filtering locally
        is much cheaper than calling async_all() three separate times.
        """
        # Fetch every entity state currently tracked by HA.
        # Returns a list of homeassistant.core.State objects.
        all_states = self.hass.states.async_all()

        batteries = self._gather_batteries(all_states)
        offline = self._gather_offline(all_states)

        # Note: automations are fetched separately using the domain filter
        # (see _gather_automations). We don't pass all_states to it because
        # hass.states.async_all("automation") does the filtering more efficiently
        # internally than we could by filtering all_states ourselves.
        automations = self._gather_automations()

        # List comprehension: build a filtered list of only the "low" batteries.
        # [item for item in list if condition] is Python's inline loop syntax.
        low_batteries = [b for b in batteries if b["low"]]

        # Assemble the final payload dict. This is what gets JSON-serialised
        # and sent to the server. Keys map directly to the README's payload schema.
        return {
            # dt_util.utcnow() returns the current UTC time as a timezone-aware
            # datetime object. .isoformat() converts it to a string like:
            # "2026-03-22T14:35:00+00:00"
            "timestamp": dt_util.utcnow().isoformat(),

            "ha_version": HA_VERSION,

            "batteries": batteries,
            "offline_entities": offline,
            "automations": automations,

            # Summary counts for quick at-a-glance reading on the server side
            "summary": {
                "battery_count": len(batteries),
                "low_battery_count": len(low_batteries),

                # List of entity IDs with low batteries — useful for alerts
                "low_battery_entities": [b["entity_id"] for b in low_batteries],

                "offline_count": len(offline),
                "automation_count": len(automations),

                # sum(1 for a in automations if a["enabled"]) is a generator
                # expression that counts how many automations have enabled=True.
                # It's equivalent to: len([a for a in automations if a["enabled"]])
                # but slightly more memory-efficient (doesn't build a temp list).
                "automations_enabled": sum(1 for a in automations if a["enabled"]),
                "automations_disabled": sum(1 for a in automations if not a["enabled"]),
            },
        }

    def _gather_batteries(self, all_states: list) -> list:
        """
        Find all entities that report a battery level and return their data.

        BACKGROUND — HOW BATTERY DATA APPEARS IN HOME ASSISTANT:
        ----------------------------------------------------------
        HA doesn't have one standard way to report battery levels. There are
        two common patterns depending on how the device integration was written:

        PATTERN 1 — device_class battery:
          A dedicated sensor entity exists with device_class set to "battery".
          The sensor's state IS the battery level (a number as a string).
          Example entity: sensor.front_door_lock_battery
            state.state = "87"
            state.attributes = {"device_class": "battery", "unit_of_measurement": "%"}

        PATTERN 2 — battery_level attribute:
          An entity (often a device_tracker) doesn't have a separate battery
          sensor but exposes battery level as one of its attributes.
          Example entity: device_tracker.my_phone
            state.state = "home"
            state.attributes = {"battery_level": 62, "source_type": "gps", ...}

        We check both patterns. An entity gets included if either pattern matches.
        The `seen` set prevents an entity from appearing twice if it somehow
        matches both patterns (unlikely but defensive).

        WHY float(state.state) MIGHT FAIL:
          If a sensor is offline or initialising, its state might be the string
          "unavailable" or "unknown" instead of a number. Trying float("unavailable")
          raises ValueError, so we catch it and skip those entities.
        """
        # A set is used here (not a list) because set membership checks (x in seen)
        # are O(1) — constant time regardless of set size. List membership is O(n).
        seen: set[str] = set()
        batteries: list[dict] = []

        for state in all_states:
            entity_id = state.entity_id
            level: float | None = None  # Will hold the battery % if found, else None

            # --- Pattern 1: dedicated battery sensor ---
            # state.attributes is a dict-like object (ReadOnlyDict).
            # .get("device_class") returns None if the key doesn't exist,
            # avoiding a KeyError. Always use .get() on attributes, never [].
            if state.attributes.get("device_class") == "battery":
                try:
                    level = float(state.state)
                    # float("87") → 87.0   ✓
                    # float("unavailable") → raises ValueError  ✗ → caught below
                except (ValueError, TypeError):
                    # ValueError: state is a non-numeric string like "unavailable"
                    # TypeError: state is None (shouldn't happen in HA, but defensive)
                    pass  # level stays None; we'll skip this entity

            # --- Pattern 2: battery_level attribute ---
            # Only check this if Pattern 1 didn't already give us a level.
            # The "in" operator checks if the key exists in the attributes dict.
            if level is None and "battery_level" in state.attributes:
                try:
                    level = float(state.attributes["battery_level"])
                except (ValueError, TypeError):
                    pass

            # Skip this entity if we couldn't find a usable battery level,
            # or if we've already included it (shouldn't happen but safe).
            if level is None or entity_id in seen:
                continue

            seen.add(entity_id)

            batteries.append(
                {
                    "entity_id": entity_id,

                    # friendly_name is the human-readable name set in HA
                    # (e.g. "Front Door Lock Battery" instead of
                    # "sensor.front_door_lock_battery"). Fall back to entity_id
                    # if the attribute isn't set.
                    "friendly_name": state.attributes.get("friendly_name", entity_id),

                    "level": level,

                    # Most battery sensors use "%". Some might use a different
                    # unit; we read whatever HA has stored, defaulting to "%".
                    "unit": state.attributes.get("unit_of_measurement", "%"),

                    # Boolean flag: True if level is at or below the threshold.
                    # <= means "less than or equal to", so 20% battery with a
                    # threshold of 20 IS flagged as low.
                    "low": level <= self._low_battery_threshold,
                }
            )

        return batteries

    def _gather_offline(self, all_states: list) -> list:
        """
        Find all entities that are currently unavailable or unknown.

        WHAT "unavailable" MEANS IN HOME ASSISTANT:
          HA marks an entity as "unavailable" when it cannot get a reading
          from the device. Common reasons:
            - The device is powered off
            - The device lost its network/Zigbee/Z-Wave connection
            - The integration that manages the device lost connection to its
              cloud service or hub
            - The Raspberry Pi lost network access to the device

        WHAT "unknown" MEANS:
          "unknown" usually means HA knows the entity exists but hasn't
          received its initial state yet (common briefly after startup).
          Persistent "unknown" states may indicate a problem.

        Both states are imported from homeassistant.const as:
          STATE_UNAVAILABLE = "unavailable"
          STATE_UNKNOWN     = "unknown"

        Using the constants instead of raw strings prevents typos.
        """
        offline = []

        for state in all_states:
            # The `in` operator on a tuple checks if the value equals any item.
            # This is equivalent to:
            #   if state.state == "unavailable" or state.state == "unknown":
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                offline.append(
                    {
                        "entity_id": state.entity_id,
                        "friendly_name": state.attributes.get(
                            "friendly_name", state.entity_id
                        ),
                        "state": state.state,  # "unavailable" or "unknown"

                        # domain is the part before the dot in the entity_id.
                        # e.g. "sensor" from "sensor.outdoor_temperature"
                        # Knowing the domain helps you understand what type of
                        # entity is offline (sensor, switch, light, etc.)
                        "domain": state.domain,

                        # last_updated is when this entity's state or attributes
                        # last changed — useful for knowing how long it's been offline.
                        # It's a datetime object, so we call .isoformat() to turn it
                        # into a JSON-friendly string.
                        # The `if state.last_updated else None` guard is defensive —
                        # last_updated should always exist but we never assume.
                        "last_updated": state.last_updated.isoformat()
                        if state.last_updated
                        else None,
                    }
                )

        return offline

    def _gather_automations(self) -> list:
        """
        Collect the status of every automation defined in HA.

        ABOUT HA AUTOMATIONS:
          Automations in HA are entities in the "automation" domain.
          Their entity IDs look like: automation.morning_lights
          Their state is one of:
            "on"  → the automation is enabled (will trigger when its conditions are met)
            "off" → the automation is disabled (won't trigger)

          Key attributes:
            last_triggered → a datetime of when the automation last ran,
                             or None if it has never been triggered
            friendly_name  → human-readable name

          NOTE: HA doesn't natively track whether an automation "failed".
          The last_triggered time just tells you when it last ran. If you need
          failure detection, you'd need to inspect automation traces via the
          HA websocket API — that's beyond the scope of this PoC.

        WHY hass.states.async_all("automation") INSTEAD OF FILTERING all_states:
          We pass the domain string "automation" directly to async_all(), which
          tells HA to filter internally. This is more efficient than receiving
          all entities and filtering them ourselves — especially on large instances.
        """
        automations = []

        for state in self.hass.states.async_all("automation"):
            # Get the last_triggered attribute. It could be:
            #   - A datetime object (most HA versions)
            #   - An ISO string (some older versions)
            #   - None (automation has never been triggered)
            last_triggered = state.attributes.get("last_triggered")

            # Normalise to a string so JSON serialisation always works the same way.
            # isinstance(x, datetime) checks if x is a datetime object.
            if isinstance(last_triggered, datetime):
                last_triggered = last_triggered.isoformat()
            elif last_triggered is not None:
                # If it's already a string or some other type, convert to string
                last_triggered = str(last_triggered)
            # If it's None, we leave it as None — JSON will serialise it as null

            automations.append(
                {
                    "entity_id": state.entity_id,
                    "friendly_name": state.attributes.get(
                        "friendly_name", state.entity_id
                    ),

                    # True if the automation is enabled, False if disabled.
                    # In HA, state "on" = enabled, "off" = disabled.
                    "enabled": state.state == "on",

                    "last_triggered": last_triggered,
                }
            )

        return automations

    # ==========================================================================
    # HTTP POST
    # ==========================================================================

    async def _post_payload(self, payload: dict) -> None:
        """
        HTTP POST the health payload to the configured server as JSON.

        WHY async?
          Network I/O (sending data over a socket, waiting for a response) can
          take milliseconds to seconds. If we used a blocking (non-async) HTTP
          call, the entire HA event loop would freeze while waiting — other
          integrations, automations, and UI responses would all stall.

          By using aiohttp (async HTTP) with `async with` and `await`, we
          "yield" control back to the event loop while waiting for the network.
          HA continues processing other things; we resume when the response arrives.

        ABOUT async_get_clientsession:
          Returns HA's shared, managed aiohttp.ClientSession. Key points:
            - Do NOT create your own aiohttp.ClientSession — HA owns this one
              and closes it cleanly on shutdown. If you create your own and don't
              close it, Python will warn about resource leaks.
            - The session includes safety middleware (SSRF protection).
            - It's safe to call this function multiple times — it always returns
              the same cached session object.

        ABOUT aiohttp.ClientTimeout:
          aiohttp won't time out by default — it will wait forever. We set a
          10-second total timeout so that if our mock server is down or slow,
          the integration logs an error and moves on rather than hanging.
          HTTP_TIMEOUT_SECONDS is defined in const.py.

        ABOUT `async with`:
          This is Python's async context manager. It's equivalent to:
            response = await session.post(...)
            try:
                ... use response ...
            finally:
                response.release()  # free the connection back to the pool
          The `async with` form handles the cleanup automatically even if an
          exception occurs inside the block.

        ERROR HANDLING:
          aiohttp.ClientError is the base class for all aiohttp exceptions:
            - aiohttp.ClientConnectorError → can't connect to the server
            - aiohttp.ServerTimeoutError   → server took too long to respond
            - aiohttp.ClientResponseError  → non-2xx status (if raise_for_status used)
          We catch the base class so we handle all network-related errors.
          We log them at ERROR level so they appear prominently in HA logs.
        """
        # Get HA's shared HTTP session.
        session = async_get_clientsession(self.hass)

        try:
            # Build a timeout object telling aiohttp to give up after N seconds.
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

            # session.post() sends an HTTP POST request.
            # The `json=payload` argument tells aiohttp to:
            #   1. JSON-serialise the `payload` dict into a string
            #   2. Set the Content-Type header to "application/json"
            # We don't need to call json.dumps() ourselves — aiohttp does it.
            async with session.post(self._url, json=payload, timeout=timeout) as resp:
                if resp.status == 200:
                    # 200 OK — the server received and accepted our data
                    _LOGGER.debug(
                        "Health report sent successfully to %s", self._url
                    )
                else:
                    # Any other status code (e.g. 404, 500) — log a warning
                    # but don't crash. The next scheduled update will try again.
                    _LOGGER.warning(
                        "Health Reporter: server returned HTTP %s for %s",
                        resp.status,
                        self._url,
                    )

        except aiohttp.ClientError as err:
            # Network-level failure (server unreachable, timeout, DNS failure, etc.)
            # Log the error and return — the next tick will try again automatically.
            _LOGGER.error(
                "Health Reporter: failed to reach server at %s — %s",
                self._url,
                err,
            )
