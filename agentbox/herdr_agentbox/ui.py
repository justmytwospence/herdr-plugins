"""The box pane's screens: a status card, and live progress for creating and resuming.

    t1  ·  paused
    justmytwospence/herdr-repeat-navigation  ·  NUC  ·  claude
    ✳ README and repeat.py review

    Enter  resume and attach   a few seconds
    q      plain shell

    Resumed anywhere else, it attaches here on its own.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, hub
from .progress import BOLD, DIM, GREEN, RED, RESET, Progress

YELLOW = "\x1b[33m"
CLEAR = "\x1b[2J\x1b[H"

STATUS_COLOR = {"running": GREEN, "paused": YELLOW, "stopped": YELLOW, "creating": DIM, "error": RED}


def where(b: Dict[str, Any]) -> str:
    provider = str(b.get("provider") or "")
    if provider in ("remote-docker", "docker"):
        return "NUC"
    return provider.capitalize() if provider else "?"


def card(title: str, status: str, b: Optional[Dict[str, Any]], keys: List[Tuple[str, str]],
         footer: str = "", hint: str = "", agent: str = "") -> str:
    color = STATUS_COLOR.get(status, DIM)
    out = ["", "  %s%s%s  ·  %s%s%s" % (BOLD, title, RESET, color, status, RESET)]
    if b:
        repo = str(b.get("repo") or "")
        out.append("  %s" % "  ·  ".join(x for x in (repo, where(b), agent or str(b.get("agent") or "")) if x))
        titles = [str(v.get("sessionTitle")) for v in (b.get("agentStatus") or {}).values() if v.get("sessionTitle")]
        task = titles[0] if titles else (b.get("task") if b.get("task") != b.get("name") else "")
        if task:
            out.append("  %s%s%s" % (DIM, task, RESET))
        if status == "error" and b.get("error"):
            out.append("  %s%s%s" % (RED, b["error"], RESET))
    out.append("")
    width = max([len(k) for k, _ in keys] + [5])
    for key, what in keys:
        out.append("  %s%s%s  %s" % (BOLD, key.ljust(width), RESET, what))
    if footer:
        out += ["", "  %s%s%s" % (DIM, footer, RESET)]
    if hint:
        out += ["", "  %s" % hint]
    return CLEAR + "\n".join(out) + "\n"


def show(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


# ---- creating a box: phases read off the hub's job log ---------------------------

def create_phases(agent: str, provider: str) -> List[Tuple[str, str, float]]:
    cloud = not provider.startswith(("docker", "remote-docker"))
    return [
        ("queue", "Queued on the hub", 3),
        ("clone", "Clone the repo", 5),
        ("provision", "Create the box", 20 if cloud else 12),
        ("seed", "Agents and logins", 75 if cloud else 60),
        ("boot", "Start the box", 12),
        ("agent", "Start %s" % agent, 12),
    ]


# First match wins; a line names the phase that is starting.
PHASE_LINES = [
    (re.compile(r"\bcloning\b|leasing a push token"), "clone"),
    (re.compile(r"\bprovisioning\b"), "provision"),
    (re.compile(r"seeding /workspace|installing \S+ \(absent|staging host credentials|credentials seeded"), "seed"),
    (re.compile(r"in-box bootstrap|agentbox-ctl bootstrap"), "boot"),
    (re.compile(r"\bstarting \S+ in\b|created box"), "agent"),
]


def phase_of(line: str) -> Optional[str]:
    for pattern, key in PHASE_LINES:
        if pattern.search(line):
            return key
    return None


def follow_create(job_id: str, title: str, subtitle: str, agent: str, provider: str,
                  stream: Any = None) -> Dict[str, Any]:
    """Live phases for a hub create job on `stream` (the terminal); returns the job's final view."""
    stream = stream or sys.stdout
    log_path = config.state_path("logs", "create-%s.log" % job_id.replace("/", "_"))
    prog = Progress(title, subtitle, create_phases(agent, provider), log_path, stream=stream)
    stop = threading.Event()

    def on_line(line: str) -> None:
        prog.log.write(line + "\n")
        key = phase_of(line)
        if key:
            prog.advance(key)

    with prog:
        prog.advance("queue")
        follower = threading.Thread(target=hub.follow_job_log, args=(job_id, on_line, stop), daemon=True)
        follower.start()
        job = hub.wait_job(job_id, stop)
        stop.set()
        if job.get("status") == "done":
            prog.finish()
        else:
            prog.fail(str(job.get("error") or job.get("status") or "failed")[:80])
    if job.get("status") != "done":
        stream.write("\n".join("  %s%s%s" % (DIM, line, RESET) for line in prog.tail(10)) + "\n")
        stream.flush()
    return job


# ---- resuming ------------------------------------------------------------------------

def resume(box_id: str, b: Dict[str, Any], is_running: Callable[[], bool]) -> Optional[str]:
    """Resume (or start) a box with a progress display; returns an error or None."""
    action = "resume" if b.get("status") == "paused" else "start"
    cloud = where(b) != "NUC"
    log_path = config.state_path("logs", "resume-%s.log" % box_id)
    phases = [("resume", "Resume the box" if action == "resume" else "Start the box", 25 if cloud else 3),
              ("ready", "Box running", 8 if cloud else 2)]
    show(CLEAR)
    prog = Progress(hub.label(b), "  ·  ".join(x for x in (str(b.get("repo") or ""), where(b)) if x),
                    phases, log_path, stream=sys.stdout)
    error = None
    with prog:
        try:
            with prog.step("resume"):
                hub.lifecycle(box_id, action)
            with prog.step("ready"):
                for _ in range(60):
                    if is_running():
                        break
                    time.sleep(2)
        except hub.HubError as e:
            error = str(e)
            prog.log.write(error + "\n")
    return error

