#!/usr/bin/env python3
"""Hold finished agents in the Agents list's "done" slot until released.

Herdr marks a completion seen when its pane is focused, so the agent drops from the
done slot to idle just for being looked at. This plugin replaces the Agents list
ordering with one keyed on pane tokens it maintains itself:

  rank: 0 blocked, 1 finished and not released, 2 working, 3 idle, 4 unknown
  held: "done" while an agent is in rank 1 (render it with a `$held` sidebar token)

An agent is held when its latest completion (`completion_seq`) is newer than the last
one released for that pane. It releases itself when it starts new work (the next
completion has a new sequence number), or explicitly with the `release` actions.

Usage: hold_done.py sync | release | release-all
Talks to the server through HERDR_SOCKET_PATH. Stdlib only.
"""

import json
import os
import socket
import sys
import time

SOURCE = "plugin:hold-done"
MAX_REPLY_BYTES = 8 * 1024 * 1024
SORT = [
    {"field": {"token": "rank"}, "order": "asc"},
    {"field": "state_change_seq", "order": "desc"},
]


class HerdrError(Exception):
    pass


def socket_path():
    return os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def call(method, params, timeout=5.0):
    request = {"id": "hold-done:%s:%d" % (method, time.time_ns()), "method": method, "params": params}
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


def state_file():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.local/state/herdr-hold-done")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "released.json")


def load_released():
    try:
        with open(state_file()) as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def save_released(released):
    tmp = state_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(released, f)
    os.replace(tmp, state_file())


def desired_tokens(agent, released):
    """The (rank, held) tokens an agent should carry; held is None when not held."""
    status = agent.get("agent_status")
    if status == "blocked":
        return "0", None
    if status == "working":
        return "2", None
    if status in ("idle", "done"):
        seq = agent.get("completion_seq")
        if seq is not None and seq > released.get(agent["pane_id"], 0):
            return "1", "done"
        return "3", None
    return "4", None


def apply_view():
    call("agent.view.set", {"source": SOURCE, "label": "hold done", "sort": SORT})


def sync():
    released = load_released()
    agents = call("agent.list", {}).get("agents", [])
    for agent in agents:
        rank, held = desired_tokens(agent, released)
        have = agent.get("tokens") or {}
        if have.get("rank") == rank and have.get("held") == held:
            continue
        call("pane.report_metadata", {
            "pane_id": agent["pane_id"],
            "source": SOURCE,
            "tokens": {"rank": rank, "held": held},
        })
    # Forget panes that no longer host an agent.
    live = {a["pane_id"] for a in agents}
    pruned = {k: v for k, v in released.items() if k in live}
    if pruned != released:
        save_released(pruned)
    apply_view()


def release(pane_ids):
    agents = {a["pane_id"]: a for a in call("agent.list", {}).get("agents", [])}
    released = load_released()
    for pid in pane_ids:
        seq = (agents.get(pid) or {}).get("completion_seq")
        if seq is not None:
            released[pid] = max(seq, released.get(pid, 0))
    save_released(released)
    sync()


def main(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "release":
        pane = os.environ.get("HERDR_PANE_ID")
        if not pane:
            raise HerdrError("release needs a focused pane (HERDR_PANE_ID)")
        release([pane])
    elif cmd == "release-all":
        release([a["pane_id"] for a in call("agent.list", {}).get("agents", [])])
    else:
        print("usage: hold_done.py sync | release | release-all", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except HerdrError as e:
        print("hold-done: %s" % e, file=sys.stderr)
        sys.exit(1)
