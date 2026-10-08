"""`agentbox-space box [<id>]`: what a box's pane runs.

While the box runs, it is `agentbox attach --inline <id>`: the agent's terminal,
with AgentBox's footer and its agent state reported to this herdr pane. Around
that it is a small screen:

- paused or stopped: Enter resumes the box and attaches; a box resumed anywhere
  else (web UI, phone, another attach) is attached on its own;
- detached (Ctrl-a d): Enter reattaches, s opens a shell in the box;
- q parks the pane in a plain shell; `agentbox-space box` there comes back.

The id defaults to the one the pane's directory names (config.box_dir).
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from typing import Any, Dict, List, Optional

from . import config, herdr, hub, state

POLL_S = 4.0
CLEAR = "\033[2J\033[H"
DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def agentbox() -> str:
    return shutil.which("agentbox") or "agentbox"


def read_key(timeout: float) -> Optional[str]:
    """One key, or None after `timeout` seconds. Enter reads as "\\n"."""
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        r, _, _ = select.select([fd], [], [], timeout)
        if not r:
            return None
        ch = os.read(fd, 1).decode(errors="replace")
        return "\n" if ch in ("\r", "\n") else ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def screen(title: str, lines: List[str], keys: str) -> None:
    out = [CLEAR, "%s%s%s\n\n" % (BOLD, title, RESET)]
    out += ["  %s\n" % line for line in lines if line]
    out.append("\n%s%s%s\n" % (DIM, keys, RESET))
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def describe(b: Dict[str, Any]) -> List[str]:
    agents = b.get("agentStatus") or {}
    states = ", ".join("%s %s" % (k, v.get("state")) for k, v in agents.items()) or str(b.get("agent") or "")
    return [
        "%s on %s" % (b.get("repo") or "?", b.get("provider") or "?"),
        "agent: %s" % states,
        ("task: %s" % b["task"]) if b.get("task") and b.get("task") != b.get("name") else "",
    ]


def report_paused(b: Dict[str, Any], status: str) -> None:
    """Keep herdr's sidebar honest while the box is not running."""
    pane = os.environ.get("HERDR_PANE_ID")
    agent = box_agent(b)
    if not pane or agent in ("", "shell"):
        return
    herdr.quiet("pane.report_agent", {
        "pane_id": pane, "source": "agentbox:%s" % agent, "agent": agent,
        "state": "idle", "message": "%s · %s" % (agent, status), "custom_status": hub.label(b),
    })


def box_agent(b: Dict[str, Any]) -> str:
    """The box's agent. The hub's `agent` field is not it: 0.33.0 lists every box as
    `claude` until it records a `lastAgent`, so a pi box would get a Claude session.
    The agents reporting state come first, then what the picker created it with."""
    reporting = list((b.get("agentStatus") or {}).keys())
    if len(reporting) == 1:
        return reporting[0]
    try:
        with open(os.path.join(config.box_dir(b["id"]), ".agent")) as f:
            recorded = f.read().strip()
        if recorded:
            return recorded
    except (OSError, KeyError):
        pass
    return str(b.get("agent") or "")


def attach_argv(box_id: str, agent: str) -> List[str]:
    """`agentbox <agent> attach` starts the agent's session when none runs (the
    generic `agentbox attach` only joins one); a shell-only box gets a shell."""
    if agent == "shell":
        return [agentbox(), "shell", box_id]
    if agent in config.settings()["agents"]:
        return [agentbox(), agent, "attach", "--inline", box_id]
    return [agentbox(), "attach", "--inline", box_id]


def attach(box_id: str, agent: str) -> int:
    """Run the attach, ending it when the box stops running: a paused container
    freezes the attach instead of ending it, which would leave a dead screen."""
    proc = subprocess.Popen(attach_argv(box_id, agent))
    not_running = 0
    while True:
        try:
            return proc.wait(timeout=POLL_S * 2)
        except subprocess.TimeoutExpired:
            pass
        got = fetch(box_id)
        if got["error"]:
            continue  # a hub blip is not the box stopping
        status = (got["box"] or {}).get("status")
        not_running = not_running + 1 if status != "running" else 0
        if not_running >= 2:
            stop(proc)
            return -1


def stop(proc: "subprocess.Popen[bytes]") -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    # The attach owned the terminal (raw mode, alternate screen); take it back.
    subprocess.call(["stty", "sane"])
    sys.stdout.write("\033[?1049l\033[?25h\033c")
    sys.stdout.flush()


def shell(box_id: str) -> int:
    return subprocess.call([agentbox(), "shell", box_id])


def fetch(box_id: str) -> Dict[str, Any]:
    """{"box": dict | None, "error": str | None}"""
    try:
        return {"box": hub.box(box_id), "error": None}
    except hub.HubError as e:
        return {"box": None, "error": str(e)}


def next_step(status: Optional[str], detached: bool) -> str:
    """What the pane does for a box status: attach | detached | paused | wait | error | gone."""
    if status is None:
        return "gone"
    if status == "running":
        return "detached" if detached else "attach"
    if status in ("paused", "stopped"):
        return "paused"
    if status == "creating":
        return "wait"
    return "error"


def run(box_id: str) -> int:
    state.unpark(box_id)
    detached = False
    while True:
        got = fetch(box_id)
        if got["error"]:
            screen("hub unreachable", [got["error"]], "retrying every few seconds · q: plain shell")
            if read_key(POLL_S) == "q":
                return park(box_id)
            continue
        b = got["box"]
        step = next_step(b.get("status") if b else None, detached)
        if step == "attach":
            attach(box_id, box_agent(b or {"id": box_id}))
            again = fetch(box_id)["box"]
            # Still running after the attach ended: the user detached.
            detached = bool(again and again.get("status") == "running")
            continue
        assert b is not None or step == "gone"
        if step == "gone":
            screen("%s no longer exists" % box_id, ["destroyed, or not on this hub"],
                   "this space closes on its own · q: plain shell now")
            if read_key(30) == "q":
                return 0
            continue
        name = hub.label(b)
        if step == "detached":
            screen("%s: detached" % name, describe(b), "Enter: reattach · s: shell in the box · q: plain shell")
            key = read_key(POLL_S * 5)
            if key == "\n":
                detached = False
            elif key == "s":
                shell(box_id)
            elif key == "q":
                return park(box_id)
            continue
        if step == "paused":
            report_paused(b, b["status"])
            screen("%s is %s" % (name, b["status"]), describe(b),
                   "Enter: resume and attach · q: plain shell (resumed elsewhere, it attaches here)")
            key = read_key(POLL_S)
            if key == "\n":
                sys.stdout.write("\nresuming %s...\n" % name)
                sys.stdout.flush()
                try:
                    hub.lifecycle(box_id, "resume" if b["status"] == "paused" else "start")
                except hub.HubError as e:
                    screen("could not resume %s" % name, [str(e)], "any key")
                    read_key(60)
                detached = False
            elif key == "q":
                return park(box_id)
            continue
        if step == "wait":
            screen("%s is being created" % name, describe(b), "attaches when ready · q: plain shell")
            if read_key(POLL_S) == "q":
                return park(box_id)
            continue
        screen("%s: %s" % (name, b.get("status")), describe(b) + [str(b.get("error") or "")],
               "Enter: try to start it · q: plain shell")
        key = read_key(POLL_S * 5)
        if key == "\n":
            try:
                hub.lifecycle(box_id, "start")
            except hub.HubError as e:
                screen("could not start %s" % name, [str(e)], "any key")
                read_key(60)
        elif key == "q":
            return park(box_id)


def park(box_id: str) -> int:
    state.park(box_id)
    sys.stdout.write(CLEAR + "Parked. `agentbox-space box` here brings %s back.\n" % box_id)
    return 0


def main(box_id: Optional[str]) -> int:
    box_id = box_id or config.box_of_dir(os.getcwd())
    if not box_id:
        print("agentbox-space box: not in a box pane directory; pass the box id", file=sys.stderr)
        return 2
    try:
        return run(box_id)
    except KeyboardInterrupt:
        return park(box_id)
