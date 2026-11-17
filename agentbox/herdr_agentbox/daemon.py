"""`agentbox-space daemon`: keeps one herdr space per hub box. Started once by
the plugin's startup hook (`--ensure`), in the herdr server it was installed in.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from typing import Dict, List, Optional

from . import config, herdr, hub, plan, state


def log(msg: str) -> None:
    path = config.state_path("logs", "daemon.log")
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(path, "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))


def _pidfile() -> str:
    return config.state_path("daemon.pid")


def running_pid() -> Optional[int]:
    try:
        with open(_pidfile()) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


PLUGIN_ID = "agentbox-spaces"
WRAPPER_MARK = "# herdr-agentbox wrapper"


def wrapper_script() -> str:
    """~/.local/bin/agentbox-space: finds the plugin when it runs, not when it was written.

    herdr (0.9.4+) installs each plugin generation in its own directory and deletes old
    ones at server start, so a link into PLUGIN_ROOT can dangle until the startup hook
    runs again. The wrapper asks herdr for the current root, then falls back to this one.
    """
    stable = config.home(".local", "share", "plugins", "herdr-agentbox")
    return """#!/bin/sh
%(mark)s: written by the plugin's startup hook; edits are overwritten.
root=%(stable)s
[ -f "$root/agentbox-space.py" ] || root=$("${HERDR_BIN_PATH:-herdr}" plugin list --plugin %(id)s --json 2>/dev/null |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["plugins"][0]["plugin_root"])' 2>/dev/null)
[ -f "$root/agentbox-space.py" ] || root=%(fallback)s
exec python3 -B "$root/agentbox-space.py" "$@"
""" % {"mark": WRAPPER_MARK, "stable": shlex.quote(stable), "id": PLUGIN_ID,
       "fallback": shlex.quote(config.PLUGIN_ROOT)}


def link_command() -> None:
    """Write ~/.local/bin/agentbox-space (wrapper_script), so panes and the user can call
    it by name. Replaces an old symlink or wrapper, never a file of the user's own."""
    link = config.home(".local", "bin", "agentbox-space")
    script = wrapper_script()
    try:
        if not os.path.islink(link) and os.path.exists(link):
            with open(link) as f:
                current = f.read()
            if current == script or WRAPPER_MARK not in current:
                return
        os.makedirs(os.path.dirname(link), exist_ok=True)
        tmp = link + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        with open(tmp, "w") as f:
            f.write(script)
        os.chmod(tmp, 0o755)
        os.replace(tmp, link)
    except OSError as e:
        log("could not write %s: %s" % (link, e))


def _script() -> str:
    return os.path.join(config.PLUGIN_ROOT, "agentbox-space.py")


def daemon_script(pid: int) -> Optional[str]:
    """The agentbox-space.py a running daemon was started from."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
    except OSError:
        try:
            out = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        argv = out.split()
    return next((a for a in argv if a.endswith("agentbox-space.py")), None)


def stop_stale(pid: int) -> bool:
    """Stop a daemon started from another plugin generation; True when it is gone."""
    script = daemon_script(pid)
    if script is None or os.path.realpath(script) == os.path.realpath(_script()):
        return False
    log("stopping daemon %d from %s (plugin now at %s)" % (pid, script, config.PLUGIN_ROOT))
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    for _ in range(50):
        time.sleep(0.1)
        if running_pid() != pid:
            return True
    return False


def ensure() -> int:
    """Start the daemon detached unless it already runs."""
    link_command()
    pid = running_pid()
    if pid and not stop_stale(pid):
        return 0
    with open(config.state_path("logs", "daemon.out"), "a") as out:
        subprocess.Popen(
            [sys.executable, "-B", _script(), "daemon"],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True, close_fds=True,
            env=dict(os.environ),
        )
    return 0


def pane_command(box_id: str) -> str:
    return "%s box %s" % (shlex.quote(config.command_path()), shlex.quote(box_id))


def run_in_pane(pane_id: str, command: str, path: Optional[str]) -> None:
    herdr.call("pane.send_text", {"pane_id": pane_id, "text": command}, path)
    herdr.call("pane.send_keys", {"pane_id": pane_id, "keys": ["enter"]}, path)


def is_shell(pane_id: str, path: Optional[str]) -> bool:
    info = herdr.call("pane.process_info", {"pane_id": pane_id}, path).get("process_info") or {}
    procs = info.get("foreground_processes") or []
    shell_pid = info.get("shell_pid")
    return not procs or all(p.get("pid") == shell_pid for p in procs)


def open_space(box: Dict, path: Optional[str]) -> str:
    result = herdr.call("workspace.create", {
        "cwd": config.box_dir(box["id"]), "label": hub.label(box), "focus": False,
    }, path)
    pane_id = result["root_pane"]["pane_id"]
    run_in_pane(pane_id, pane_command(box["id"]), path)
    return result["workspace"]["workspace_id"]


def tick(path: Optional[str], gone_ticks: Dict[str, int]) -> List[str]:
    """One comparison of hub and herdr; returns what it did, for the log."""
    boxes = hub.boxes()
    panes = herdr.call("pane.list", {}, path).get("panes") or []
    workspaces = herdr.call("workspace.list", {}, path).get("workspaces") or []
    todo = plan.plan(boxes, panes, workspaces, config.box_of_dir,
                     lambda pane_id: is_shell(pane_id, path),
                     state.pending_names(), state.parked(), gone_ticks)
    done = []
    for b in todo.create:
        open_space(b, path)
        done.append("opened %s (%s)" % (hub.label(b), b["id"]))
    for r in todo.restart:
        run_in_pane(r["pane_id"], pane_command(r["box_id"]), path)
        done.append("reattached %s in %s" % (r["box_id"], r["pane_id"]))
    for r in todo.rename:
        herdr.quiet("workspace.rename", {"workspace_id": r["workspace_id"], "label": r["label"]}, path)
        done.append("renamed %s to %s" % (r["workspace_id"], r["label"]))
    for ws in todo.close:
        herdr.quiet("workspace.close", {"workspace_id": ws}, path)
        done.append("closed %s (box gone)" % ws)
    return done


def main() -> int:
    other = running_pid()
    if other and other != os.getpid():
        return 0
    with open(_pidfile(), "w") as f:
        f.write(str(os.getpid()))
    path = os.environ.get("HERDR_SOCKET_PATH") or herdr.socket_path()
    log("daemon started (pid %d, herdr %s, hub %s)" % (os.getpid(), path, config.hub()["url"] or "?"))
    # Let herdr finish restoring its layout first, or restored panes look missing.
    time.sleep(float(os.environ.get("AGENTBOX_SPACE_START_DELAY", "8")))
    gone_ticks: Dict[str, int] = {}
    failing = ""
    while True:
        try:
            for line in tick(path, gone_ticks):
                log(line)
            if failing:
                log("recovered")
            failing = ""
        except (hub.HubError, herdr.Unavailable, herdr.HerdrError) as e:
            if str(e) != failing:
                log("tick failed: %s" % e)
            failing = str(e)
        except Exception as e:  # keep the daemon alive; the log says why
            log("tick crashed: %r" % e)
        time.sleep(float(config.settings()["poll_s"]))
