"""
mock_server/server.py — Test HTTP server for CI Health Reporter
================================================================

PURPOSE:
  This is a simple HTTP server we run on a machine on the local network
  (192.168.1.189 in this project) to receive and display the JSON health
  reports sent by the HA integration.

  It is NOT part of the Home Assistant integration itself — it's a
  standalone testing and development tool.

WHAT IT DOES:
  - Listens on a port (default 8765) for HTTP POST requests to /health
  - Reads the JSON body of each request
  - Pretty-prints the JSON to the terminal so we can inspect it
  - Responds with 200 OK so the integration knows the data was received

WHY BaseHTTPRequestHandler?
  Python's standard library includes a simple HTTP server framework in the
  `http.server` module. We use it here because:
    - No third-party libraries needed (zero dependencies)
    - Simple enough to read and understand in minutes
    - Sufficient for testing — we don't need production features like
      async handling, TLS, or routing

  For a real server (production use), you'd use something like:
    - FastAPI  (async, modern, automatic docs)
    - Flask    (simple, widely used)
    - aiohttp  (async, matches HA's HTTP stack)

USAGE:
  python server.py [port]

  Examples:
    python server.py          # listen on default port 8765
    python server.py 9000     # listen on port 9000

HOW TO TEST WITHOUT HOME ASSISTANT:
  You can send a fake payload using curl:
    curl -X POST http://localhost:8765/health \
         -H "Content-Type: application/json" \
         -d '{"timestamp": "2026-03-22T14:00:00Z", "summary": {}}'
"""

import json   # Python's built-in JSON parser and serialiser
import sys    # Provides access to command-line arguments (sys.argv)
from http.server import BaseHTTPRequestHandler, HTTPServer
# BaseHTTPRequestHandler → base class we subclass to handle requests
# HTTPServer             → the server that listens on the port and dispatches requests


# ---------------------------------------------------------------------------
# REQUEST HANDLER CLASS
# ---------------------------------------------------------------------------
# When a client (our HA integration) connects and sends a request, HTTPServer
# creates a new instance of this class and calls the appropriate method:
#
#   HTTP GET  → do_GET()
#   HTTP POST → do_POST()
#   HTTP PUT  → do_PUT()
#   etc.
#
# We only implement do_POST because that's all we need. Any other HTTP method
# will return a 501 Not Implemented response automatically (from the base class).
class HealthHandler(BaseHTTPRequestHandler):
    """Handles incoming POST requests to /health."""

    def do_POST(self):
        """
        Called automatically by HTTPServer when an HTTP POST request arrives.

        `self` gives us access to:
          self.path     → the URL path, e.g. "/health"
          self.headers  → the HTTP request headers as a dict-like object
          self.rfile    → a file-like object to READ the request body from
          self.wfile    → a file-like object to WRITE the response body to
          self.send_response(code)      → write the status line (e.g. "200 OK")
          self.send_header(name, value) → add a response header
          self.end_headers()            → write the blank line ending the headers
        """

        # Only handle requests to /health. If something hits a different path,
        # return 404 Not Found. This makes the server predictable — it only
        # accepts the one endpoint our integration uses.
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return   # `return` exits do_POST early; nothing more to do

        # -------------------------------------------------------------------
        # READ THE REQUEST BODY
        # -------------------------------------------------------------------
        # HTTP POST requests include a body (the payload). To read it, we
        # first need to know how many bytes to read.
        #
        # The sender (aiohttp in coordinator.py) sets the "Content-Length"
        # header to the byte count of the body. We read it here.
        #
        # .get("Content-Length", 0) returns 0 if the header is missing, which
        # prevents a crash if a malformed request arrives without that header.
        # int(...) converts the string header value to an integer.
        content_length = int(self.headers.get("Content-Length", 0))

        # self.rfile is a binary file-like object (like an open file).
        # .read(n) reads exactly n bytes and returns them as a bytes object.
        # e.g. b'{"timestamp": "2026-03-22T14:00:00Z", ...}'
        body = self.rfile.read(content_length)

        # -------------------------------------------------------------------
        # PARSE THE JSON BODY
        # -------------------------------------------------------------------
        # json.loads() converts a JSON string (or bytes) to a Python dict.
        # If the body is not valid JSON, it raises json.JSONDecodeError.
        # We catch it so the server doesn't crash on a bad request.
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            print(f"[ERROR] Failed to parse JSON: {exc}")

            # 400 Bad Request: the client sent data we couldn't parse
            self.send_response(400)
            self.end_headers()
            return

        # -------------------------------------------------------------------
        # DISPLAY THE PAYLOAD
        # -------------------------------------------------------------------
        # json.dumps(data, indent=2) serialises the Python dict back to a
        # JSON string, but with 2-space indentation for readability.
        # This is "pretty printing" — the raw body is compact (no whitespace)
        # but this format is easy to read in the terminal.
        print("\n" + "=" * 60)
        print(f"Received health report at {data.get('timestamp', 'unknown time')}")
        print("=" * 60)
        print(json.dumps(data, indent=2))
        print("=" * 60 + "\n")

        # -------------------------------------------------------------------
        # SEND THE RESPONSE
        # -------------------------------------------------------------------
        # HTTP responses have three parts:
        #   1. Status line: "HTTP/1.1 200 OK"
        #   2. Headers: "Content-Type: application/json", "Content-Length: 15", etc.
        #   3. Body: the response payload
        #
        # send_response, send_header, end_headers must be called in that order.
        # Calling end_headers() writes the blank line that separates headers from body.

        response = json.dumps({"status": "ok"}).encode()
        # .encode() converts the Python string to bytes (UTF-8 by default).
        # HTTP sends bytes, not strings.

        self.send_response(200)   # "200 OK"
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))  # byte count of body
        self.end_headers()        # writes the blank line — MUST come before body
        self.wfile.write(response)  # write the response body bytes

    def log_message(self, format, *args):
        """
        Override the default request logging from BaseHTTPRequestHandler.

        By default, BaseHTTPRequestHandler prints a line like:
          127.0.0.1 - - [22/Mar/2026 14:35:00] "POST /health HTTP/1.1" 200 -

        We override this with an empty method to suppress that output.
        Our do_POST() already prints the payload in a cleaner format,
        so we don't need the default log line too.

        The `pass` statement means "do nothing" — it's required because Python
        doesn't allow an empty function body.
        """
        pass


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
# `if __name__ == "__main__":` is Python's way of saying:
#   "Only run this block if this file is executed directly (e.g. python server.py),
#    not if it's imported as a module by another file."
#
# This is a standard Python pattern for scripts that can also be imported.
if __name__ == "__main__":

    # sys.argv is a list of command-line arguments.
    # sys.argv[0] is always the script name ("server.py").
    # sys.argv[1] is the first argument the user typed, if any.
    #
    # Example:
    #   python server.py 9000   → sys.argv = ["server.py", "9000"]
    #   python server.py        → sys.argv = ["server.py"]
    #
    # len(sys.argv) > 1 checks if the user provided any arguments.
    # If yes, use it as the port. If no, fall back to 8765.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

    # HTTPServer(address, handler_class) creates the server socket.
    # ("0.0.0.0", port) means "listen on ALL network interfaces on this machine"
    # so the server is reachable from both localhost AND from other devices
    # on the network (including the Raspberry Pi running HA).
    # If you used ("127.0.0.1", port) it would only be reachable from localhost.
    server = HTTPServer(("0.0.0.0", port), HealthHandler)

    print(f"Mock health server listening on 0.0.0.0:{port}/health")
    print("Press Ctrl+C to stop.\n")

    try:
        # serve_forever() starts an infinite loop that:
        #   1. Waits for a connection
        #   2. Creates a HealthHandler instance for the connection
        #   3. Calls the appropriate do_XXX method
        #   4. Goes back to step 1
        # It blocks here until interrupted.
        server.serve_forever()

    except KeyboardInterrupt:
        # Ctrl+C raises KeyboardInterrupt. We catch it so we can print a clean
        # message instead of showing a confusing Python traceback.
        print("\nServer stopped.")
