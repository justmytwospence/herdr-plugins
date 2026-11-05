#!/usr/bin/env python3
"""Order the Agents list for cycling with next_agent.

Order: the agent you are on, then blocked agents you have not visited since they
blocked, then agents whose prompt cache expires soon (soonest first) that you have
not been on since, then finished agents Herdr still reports unseen (`done`), then
every other agent, least recently visited first, and last the agents whose turn only
waits on background work. next_agent from the top therefore visits what needs you
first, otherwise round-robins (the agent you leave was visited last).

"Only waits" is pi-herdr's `wait` pane token on a working agent: pi-bg's bg_wait, or
its wait for a job at the end of a turn. Such an agent needs nothing from you until
the job ends, however long ago you visited it. Token changes invoke no plugin hook,
so `start` (the startup hook and the sync action) also runs `watch`: one detached
process per server that subscribes to pane.updated over the socket and re-syncs when
a pane's wait started or ended, and exits when the server closes the connection.

"Expires soon" is pi-cache-guard's `cache_expires` pane token (13-digit epoch ms of
the expiry, present only in the last minutes of a big idle cache and dropped by
herdr at expiry). The view sorts on it directly, so an agent moves up when the
token appears without this plugin running; without cache-guard nothing changes.

Pane tokens (set with source plugin:agent-cycle, not displayed):
  cycle_blocked  "0" blocked and unvisited, else absent
  cycle_cache_ack  "1" once you were on the agent while its cache_expires was
                   present (until that value changes or goes), else "0"
  cycle_rank  "0" blocked and unvisited, "1" done, "2" everything else,
              "3" working and only waiting (pi-herdr's `wait` token)
  cycle_seen  13-digit epoch ms of the last focus ("0000000000000" if never)
  cycle_cur_<host>  "1" on this server's focused pane (see current_token)

next-block focuses the first agent in that same order that is outside the current
agent's block: a repository's spaces (its main checkout and its linked worktrees),
or a space of its own outside a repository. One press therefore steps over the rest
of a repo's worktree spaces instead of visiting them one at a time.

Usage: cycle.py start | sync | focus | watch | next-block
"""

import fcntl
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

SOURCE = "plugin:agent-cycle"
NEVER = "0" * 13
# pi-cache-guard's token: when an idle session's big prompt cache expires, near the end.
EXPIRES = "cache_expires"
# pi-herdr's token: what a working turn only waits on (a background job), while it waits.
WAIT = "wait"


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
    # Absent tokens sort after present ones.
    return [
        {"field": {"token": cur}, "order": "asc"},
        {"field": {"token": "cycle_blocked"}, "order": "asc"},
        {"field": {"token": "cycle_cache_ack"}, "order": "asc"},
        {"field": {"token": EXPIRES}, "order": "asc"},
        {"field": {"token": "cycle_rank"}, "order": "asc"},
        {"field": {"token": "cycle_seen"}, "order": "asc"},
    ]


def call(method, params):
    return herdr_rpc.call(method, params, request_id="agent-cycle")


def now_ms():
    return int(time.time() * 1000)


def stamp(ms):
    return "%013d" % ms


def state_dir():
    d = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.local/state/herdr-agent-cycle")
    os.makedirs(d, exist_ok=True)
    return d


def state_file():
    return os.path.join(state_dir(), "panes.json")


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


def expires(agent):
    return (agent.get("tokens") or {}).get(EXPIRES)


def update_pane(agent, entry, now, on_it=False):
    """Track when a pane became blocked and whether you were on it while its cache
    was expiring (`on_it`); return the updated entry."""
    entry = dict(entry)
    if agent.get("agent_status") == "blocked":
        entry.setdefault("blocked_since", now)
    else:
        entry.pop("blocked_since", None)
    exp = expires(agent)
    if exp and on_it:
        entry["ack"] = exp
    elif entry.get("ack") != exp or not exp:
        entry.pop("ack", None)
    return entry


def blocked_unvisited(agent, entry):
    return agent.get("agent_status") == "blocked" and entry.get("seen", 0) < entry.get("blocked_since", 0)


def waiting(agent):
    """Working, but the turn only waits on background work (pi-herdr's `wait` token)."""
    return agent.get("agent_status") == "working" and bool((agent.get("tokens") or {}).get(WAIT))


def rank(agent, entry):
    status = agent.get("agent_status")
    if blocked_unvisited(agent, entry):
        return "0"
    if status == "done":
        return "1"
    if waiting(agent):
        return "3"
    return "2"


def tokens_for(agent, entry, cur, is_current):
    seen = entry.get("seen")
    exp = expires(agent)
    return {
        "cycle_blocked": "0" if blocked_unvisited(agent, entry) else None,
        "cycle_cache_ack": "1" if exp and entry.get("ack") == exp else "0",
        "cycle_rank": rank(agent, entry),
        "cycle_seen": stamp(seen) if seen else NEVER,
        cur: "1" if is_current else None,
    }


def sync(focused=None, now=None):
    now = now or now_ms()
    cur = current_token()
    old = load_state()
    # On a focus change, the pane you left: the one focused last.
    left = previous_focus(old) if focused else None
    agents = call("agent.list", {}).get("agents", [])
    state = {}
    for agent in agents:
        pid = agent["pane_id"]
        is_current = pid == focused if focused else bool(agent.get("focused"))
        entry = update_pane(agent, old.get(pid, {}), now, on_it=is_current or pid == left)
        if pid == focused:
            entry["seen"] = now
        state[pid] = entry
        want = tokens_for(agent, entry, cur, is_current)
        have = agent.get("tokens") or {}
        if all(have.get(k) == v for k, v in want.items()):
            continue
        call("pane.report_metadata", {"pane_id": pid, "source": SOURCE, "tokens": want})
    if state != old:
        save_state(state)
    call("agent.view.set", {"source": SOURCE, "label": "queue", "sort": sort_spec(cur)})


def previous_focus(state):
    seen = [(e.get("seen", 0), pid) for pid, e in state.items() if isinstance(e, dict) and e.get("seen")]
    return max(seen)[1] if seen else None


def queue_key(agent, cur):
    """The view's sort key for one agent: per token, absent after present, then by value."""
    tokens = agent.get("tokens") or {}
    key = []
    for spec in sort_spec(cur):
        value = tokens.get(spec["field"]["token"])
        key.append((value is None, value or ""))
    return key


def block_of(workspace):
    """A repository's spaces share one block; any other space is a block of its own."""
    tree = (workspace or {}).get("worktree") or {}
    return tree.get("repo_key") or (workspace or {}).get("workspace_id")


def next_block_target(agents, workspaces, current, cur):
    """The first agent in queue order outside the current agent's block (or, when every
    agent shares that block, simply the next one); None when there is no other agent."""
    blocks = {w["workspace_id"]: block_of(w) for w in workspaces}

    def block(agent):
        return blocks.get(agent.get("workspace_id"), agent.get("workspace_id"))

    here = next((block(a) for a in agents if a["pane_id"] == current), None)
    others = sorted((a for a in agents if a["pane_id"] != current), key=lambda a: queue_key(a, cur))
    outside = [a for a in others if block(a) != here]
    pick = (outside or others or [None])[0]
    return pick["pane_id"] if pick else None


def next_block():
    agents = call("agent.list", {}).get("agents", [])
    workspaces = call("workspace.list", {}).get("workspaces", [])
    current = os.environ.get("HERDR_PANE_ID") or next((a["pane_id"] for a in agents if a.get("focused")), None)
    target = next_block_target(agents, workspaces, current, current_token())
    if target:
        call("agent.focus", {"target": target})


def event_json():
    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def updated_needs_sync(data):
    """Whether a pane.updated event changes the queue: the pane started or stopped only
    waiting and its rank does not say so yet. Unreadable events re-sync."""
    node = data.get("data") if isinstance(data.get("data"), dict) else data
    pane = node.get("pane")
    if not isinstance(pane, dict) or not pane.get("pane_id"):
        return True
    if not pane.get("agent"):
        return False
    ranked = (pane.get("tokens") or {}).get("cycle_rank")
    return (ranked == "3") != waiting(pane)


def watch_events(lines, resync):
    """Re-sync for every pane.updated line that starts or ends a wait, until the
    stream ends. The first line acknowledges the subscription."""
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("data"), dict) and updated_needs_sync(event):
            resync()


def watch():
    """Hold the lock, subscribe to pane.updated and re-sync on waits, until the
    server closes the connection. A second watcher exits at once."""
    lock = open(os.path.join(state_dir(), "watch.lock"), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    request = {"id": "agent-cycle:watch", "method": "events.subscribe",
               "params": {"subscriptions": [{"type": "pane.updated"}]}}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(herdr_rpc.socket_path())
        conn.sendall(json.dumps(request).encode() + b"\n")
        with conn.makefile("rb") as stream:
            def resync():
                try:
                    sync()
                except (herdr_rpc.HerdrError, OSError) as e:
                    print("agent-cycle watch: %s" % e, file=sys.stderr)
            watch_events(stream, resync)


def start_watcher():
    """Replace this server's watcher with one running this checkout's code."""
    path = os.path.join(state_dir(), "watch.lock")
    try:
        with open(path, "a+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                pid = 0  # nobody holds it: the pid in it is stale, never signal it
            except OSError:
                f.seek(0)
                pid = int(f.read().strip() or 0)
        if pid > 0:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                os.kill(pid, 0)
                time.sleep(0.05)
    except (OSError, ValueError):
        pass
    with open(os.devnull, "r+b") as null:
        subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__), "watch"],
                         stdin=null, stdout=null, stderr=null, start_new_session=True, close_fds=True)


def focused_pane_from_event():
    data = event_json()
    for node in (data.get("data") or {}, data):
        if isinstance(node, dict) and node.get("pane_id"):
            return node["pane_id"]
    return os.environ.get("HERDR_PANE_ID")


def main(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "start":
        sync()
        start_watcher()
    elif cmd == "sync":
        sync()
    elif cmd == "focus":
        sync(focused=focused_pane_from_event())
    elif cmd == "watch":
        watch()
    elif cmd == "next-block":
        next_block()
    else:
        print("usage: cycle.py start | sync | focus | watch | next-block", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (herdr_rpc.HerdrError, OSError) as e:
        print("agent-cycle: %s" % e, file=sys.stderr)
        sys.exit(1)
