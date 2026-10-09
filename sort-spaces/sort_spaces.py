#!/usr/bin/env python3
"""Sort herdr spaces (workspaces) alphabetically by label.

Usage: sort_spaces.py [--reverse] [--dry-run]

Talks to the server that spawned it through HERDR_SOCKET_PATH (plugin
processes get it set), falling back to the default socket. Stdlib only.
"""

import json
import os
import socket
import sys
import time

MAX_REPLY_BYTES = 8 * 1024 * 1024


class HerdrError(Exception):
    pass


def socket_path():
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def call(method, params, timeout=5.0):
    request = {"id": "sort-spaces:%s:%d" % (method, time.time_ns()), "method": method, "params": params}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path())
        sock.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > MAX_REPLY_BYTES:
                raise HerdrError("%s: reply too large" % method)
    except OSError as e:
        raise HerdrError("%s: %s" % (method, e))
    finally:
        sock.close()
    line = buf.split(b"\n", 1)[0]
    if not line:
        raise HerdrError("%s: empty reply" % method)
    reply = json.loads(line.decode())
    if reply.get("error"):
        err = reply["error"]
        raise HerdrError("%s: %s: %s" % (method, err.get("code"), err.get("message")))
    return reply.get("result") or {}


def list_workspaces():
    spaces = call("workspace.list", {}).get("workspaces", [])
    return sorted(spaces, key=lambda w: w["number"])


def sort_key(space):
    label = space.get("label") or ""
    # Case-insensitive, then case-sensitive, then current position (stable).
    return (label.casefold(), label, space["number"])


def target_order(spaces, reverse=False):
    ordered = sorted(spaces, key=sort_key)
    if reverse:
        ordered.reverse()
    return [w["workspace_id"] for w in ordered]


def plan_moves(current, target):
    """Moves (workspace_id, insert_index) turning `current` into `target`.

    Selection-sort style: place target[i] at index i, skipping spaces already
    in place, so an already-sorted sidebar makes no calls.
    """
    order = list(current)
    moves = []
    for i, wid in enumerate(target):
        if order[i] == wid:
            continue
        order.remove(wid)
        order.insert(i, wid)
        moves.append((wid, i))
    return moves


def main(argv):
    reverse = "--reverse" in argv
    dry_run = "--dry-run" in argv
    spaces = list_workspaces()
    current = [w["workspace_id"] for w in spaces]
    target = target_order(spaces, reverse)
    moves = plan_moves(current, target)
    labels = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in spaces}
    if not moves:
        print("Spaces already sorted.")
        return 0
    for wid, index in moves:
        print("%s %s -> %d" % ("would move" if dry_run else "move", labels[wid], index + 1))
        if not dry_run:
            call("workspace.move", {"workspace_id": wid, "insert_index": index})
    if not dry_run:
        final = [w["workspace_id"] for w in list_workspaces()]
        if final != target:
            print("warning: order after sorting differs from target", file=sys.stderr)
            return 1
    print("%s %d spaces (%d moves)." % ("Would sort" if dry_run else "Sorted", len(spaces), len(moves)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except HerdrError as e:
        print("sort-spaces: %s" % e, file=sys.stderr)
        sys.exit(1)
