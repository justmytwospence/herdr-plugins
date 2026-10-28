"""Minimal client for the Herdr socket API, shared by the plugins in this repo.

Each plugin adds this directory to sys.path (herdr checks out the whole repository,
so `<plugin_root>/../lib` exists for installed and linked plugins alike). Stdlib only.
"""

import json
import os
import socket
import time

DEFAULT_MAX_BYTES = 8 * 1024 * 1024


class HerdrError(RuntimeError):
    """An error reply from the server, or a reply that could not be read."""


def socket_path():
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def call(method, params=None, *, timeout=5.0, max_bytes=DEFAULT_MAX_BYTES, request_id="herdr-plugins"):
    """Send one request and return its `result`.

    Raises HerdrError for an error reply or an unreadable one; OSError (cannot
    connect, timeout) propagates unchanged so callers can tell the two apart.
    """
    request = {"id": "%s:%s:%d" % (request_id, method, time.time_ns()), "method": method, "params": params or {}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(socket_path())
        conn.sendall(json.dumps(request).encode() + b"\n")
        with conn.makefile("rb") as stream:
            line = stream.readline(max_bytes + 1)
    if not line:
        raise HerdrError("%s: empty reply" % method)
    if len(line) > max_bytes:
        raise HerdrError("%s: reply larger than %d bytes" % (method, max_bytes))
    try:
        reply = json.loads(line)
    except ValueError as error:
        raise HerdrError("%s: unreadable reply: %s" % (method, error)) from None
    if reply.get("error"):
        err = reply["error"]
        if isinstance(err, dict):
            raise HerdrError("%s: %s: %s" % (method, err.get("code"), err.get("message")))
        raise HerdrError("%s: %s" % (method, err))
    return reply.get("result") or {}
