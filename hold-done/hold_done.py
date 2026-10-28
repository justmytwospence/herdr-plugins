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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

SOURCE = "plugin:hold-done"
SORT = [
    {"field": {"token": "rank"}, "order": "asc"},
    {"field": "state_change_seq", "order": "desc"},
]


HerdrError = herdr_rpc.HerdrError


def call(method, params, timeout=5.0):
    return herdr_rpc.call(method, params, timeout=timeout, request_id="hold-done")


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
    except (HerdrError, OSError) as e:
        print("hold-done: %s" % e, file=sys.stderr)
        sys.exit(1)
