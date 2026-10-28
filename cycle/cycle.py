#!/usr/bin/env python3
"""Order the Agents list for cycling with next_agent.

Order: the agent you are on, then blocked agents you have not visited since they
blocked, then finished agents Herdr still reports unseen (`done`), then every other
agent, least recently visited first. next_agent from the top therefore visits what
needs you first, otherwise round-robins (the agent you leave was visited last).

Pane tokens (set with source plugin:agent-cycle, not displayed):
  cycle_rank  "0" blocked and unvisited, "1" done, "2" everything else
  cycle_seen  13-digit epoch ms of the last focus ("0000000000000" if never)
  cycle_cur_<host>  "1" on this server's focused pane (see current_token)

Usage: cycle.py sync | focus
"""

import json
import os
import re
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

SOURCE = "plugin:agent-cycle"
NEVER = "0" * 13


def current_token(host=None):
    """This server's "you are here" token name.

    Each server knows only its own focused pane, so each flags it under its own
    name and its view sorts by that name only. Herdr sorts the combined list with
    the selected machine's view, so the other machine's stale flag is ignored.
    """
    host = (host or socket.gethostname()).split(".")[0]
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", host)[:22] or "host"
    return "cycle_cur_" + safe


def sort_spec(cur):
    return [
        {"field": {"token": cur}, "order": "asc"},
        {"field": {"token": "cycle_rank"}, "order": "asc"},
        {"field": {"token": "cycle_seen"}, "order": "asc"},
    ]


def call(method, params):
    return herdr_rpc.call(method, params, request_id="agent-cycle")


def now_ms():
    return int(time.time() * 1000)


def stamp(ms):
    return "%013d" % ms


def state_file():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.local/state/herdr-agent-cycle")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "panes.json")


def load_state():
    try:
        with open(state_file()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = state_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_file())


def update_pane(agent, entry, now):
    """Track when a pane became blocked; return the updated entry."""
    entry = dict(entry)
    if agent.get("agent_status") == "blocked":
        entry.setdefault("blocked_since", now)
    else:
        entry.pop("blocked_since", None)
    return entry


def rank(agent, entry):
    status = agent.get("agent_status")
    if status == "blocked" and entry.get("seen", 0) < entry.get("blocked_since", 0):
        return "0"
    if status == "done":
        return "1"
    return "2"


def tokens_for(agent, entry, cur, is_current):
    seen = entry.get("seen")
    return {
        "cycle_rank": rank(agent, entry),
        "cycle_seen": stamp(seen) if seen else NEVER,
        cur: "1" if is_current else None,
    }


def sync(focused=None, now=None):
    now = now or now_ms()
    cur = current_token()
    old = load_state()
    agents = call("agent.list", {}).get("agents", [])
    state = {}
    for agent in agents:
        pid = agent["pane_id"]
        entry = update_pane(agent, old.get(pid, {}), now)
        if pid == focused:
            entry["seen"] = now
        state[pid] = entry
        is_current = pid == focused if focused else bool(agent.get("focused"))
        want = tokens_for(agent, entry, cur, is_current)
        have = agent.get("tokens") or {}
        if all(have.get(k) == v for k, v in want.items()):
            continue
        call("pane.report_metadata", {"pane_id": pid, "source": SOURCE, "tokens": want})
    if state != old:
        save_state(state)
    call("agent.view.set", {"source": SOURCE, "label": "queue", "sort": sort_spec(cur)})


def focused_pane_from_event():
    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        data = {}
    for node in (data.get("data") or {}, data):
        if isinstance(node, dict) and node.get("pane_id"):
            return node["pane_id"]
    return os.environ.get("HERDR_PANE_ID")


def main(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "focus":
        sync(focused=focused_pane_from_event())
    else:
        print("usage: cycle.py sync | focus", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (herdr_rpc.HerdrError, OSError) as e:
        print("agent-cycle: %s" % e, file=sys.stderr)
        sys.exit(1)
