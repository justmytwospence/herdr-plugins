#!/usr/bin/env python3
"""Order the Agents list for cycling with next_agent.

Order: blocked agents you have not visited since they blocked, then finished agents
Herdr still reports unseen (`done`), then every other agent, least recently visited
first. The agent you are on was visited last, so it sits at the bottom and
next_agent wraps to the top: what needs you first, otherwise a round-robin.

Pane tokens (set with source plugin:agent-cycle, not displayed):
  cycle_rank  "0" blocked and unvisited, "1" done, "2" everything else
  cycle_seen  13-digit epoch ms of the last focus ("0000000000000" if never)

Usage: cycle.py sync | focus
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

SOURCE = "plugin:agent-cycle"
NEVER = "0" * 13
SORT = [
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


def tokens_for(agent, entry):
    seen = entry.get("seen")
    return {"cycle_rank": rank(agent, entry), "cycle_seen": stamp(seen) if seen else NEVER}


def sync(focused=None, now=None):
    now = now or now_ms()
    old = load_state()
    agents = call("agent.list", {}).get("agents", [])
    state = {}
    for agent in agents:
        pid = agent["pane_id"]
        entry = update_pane(agent, old.get(pid, {}), now)
        if pid == focused:
            entry["seen"] = now
        state[pid] = entry
        want = tokens_for(agent, entry)
        have = agent.get("tokens") or {}
        if all(have.get(k) == v for k, v in want.items()):
            continue
        call("pane.report_metadata", {"pane_id": pid, "source": SOURCE, "tokens": want})
    if state != old:
        save_state(state)
    call("agent.view.set", {"source": SOURCE, "sort": SORT})


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
