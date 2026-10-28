#!/usr/bin/env python3
"""Hold finished agents in the Agents list's "done" slot until released.

Herdr marks a completion seen when its pane is focused, so the agent drops from the
done slot to idle just for being looked at. This plugin replaces the Agents list
ordering with one keyed on pane tokens it maintains itself:

  rank: 0 blocked, 1 finished and not released, 2 working, 3 idle, 4 unknown
  held: "done" while an agent is in rank 1 (render it with a `$held` sidebar token)

A hold starts when Herdr reports an unseen completion ("done") and survives Herdr
marking it seen on focus. It ends when the agent starts new work, or with the
`release` actions.

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
    return os.path.join(d, "held.json")


def load_held():
    try:
        with open(state_file()) as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def save_held(held):
    tmp = state_file() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(held, f)
    os.replace(tmp, state_file())


def update_held(agent, held):
    """Return the completion this pane holds after seeing `agent`, or None.

    A hold starts only when Herdr reports an unseen completion ("done"), and
    lasts, through Herdr marking it seen, until the agent starts another turn
    or finishes a newer one (which needs its own unseen "done" to hold again).
    """
    status = agent.get("agent_status")
    seq = agent.get("completion_seq")
    if status == "done" and seq is not None:
        return seq
    if status == "idle" and seq is not None and held.get(agent["pane_id"]) == seq:
        return seq
    return None


def desired_tokens(agent, is_held):
    """The (rank, held) tokens an agent should carry; held is None when not held."""
    status = agent.get("agent_status")
    if status == "blocked":
        return "0", None
    if status == "working":
        return "2", None
    if is_held:
        return "1", "done"
    if status in ("idle", "done"):
        return "3", None
    return "4", None


def apply_view():
    call("agent.view.set", {"source": SOURCE, "label": "hold done", "sort": SORT})


def sync(release=()):
    old = load_held()
    agents = call("agent.list", {}).get("agents", [])
    held = {}
    for agent in agents:
        pid = agent["pane_id"]
        seq = None if pid in release else update_held(agent, old)
        if seq is not None:
            held[pid] = seq
        rank, mark = desired_tokens(agent, seq is not None)
        have = agent.get("tokens") or {}
        if have.get("rank") == rank and have.get("held") == mark:
            continue
        call("pane.report_metadata", {"pane_id": pid, "source": SOURCE, "tokens": {"rank": rank, "held": mark}})
    if held != old:
        save_held(held)
    apply_view()


def main(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "release":
        pane = os.environ.get("HERDR_PANE_ID")
        if not pane:
            raise HerdrError("release needs a focused pane (HERDR_PANE_ID)")
        sync(release={pane})
    elif cmd == "release-all":
        sync(release={a["pane_id"] for a in call("agent.list", {}).get("agents", [])})
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
