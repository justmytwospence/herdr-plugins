"""herdr plugin actions. herdr passes the invoking context in HERDR_PLUGIN_CONTEXT_JSON."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

from . import config, daemon, herdr, hub

CONFIRM_S = 10


def focused_box() -> Optional[str]:
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    pane_id = ctx.get("pane_id") or os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return None
    pane = herdr.call("pane.get", {"pane_id": pane_id}).get("pane") or {}
    return config.box_of_dir(pane.get("cwd"))


def detached(*args: str) -> None:
    """Run a slow hub call in the background; the action returns at once."""
    with open(config.state_path("logs", "actions.log"), "a") as out:
        subprocess.Popen([sys.executable, "-B", os.path.join(config.PLUGIN_ROOT, "agentbox-space.py")] + list(args),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
                         env=dict(os.environ))


def confirmed(box_id: str) -> bool:
    """True on the second destroy of the same box within CONFIRM_S seconds."""
    marker = config.state_path("destroy-confirm")
    try:
        with open(marker) as f:
            prev_id, at = f.read().split()
        if prev_id == box_id and time.time() - float(at) < CONFIRM_S:
            os.unlink(marker)
            return True
    except (OSError, ValueError):
        pass
    with open(marker, "w") as f:
        f.write("%s %f" % (box_id, time.time()))
    return False


def lifecycle(box_id: str, action: str) -> int:
    """`agentbox-space lifecycle <id> <action>`: the background half of an action."""
    try:
        hub.lifecycle(box_id, action)
        herdr.notify("%s: %s done" % (box_id, action))
    except hub.HubError as e:
        herdr.notify("%s: %s failed" % (box_id, action), str(e)[:200])
    return 0


def run(action: str) -> int:
    if action == "sync":
        done = daemon.tick(None, {})
        herdr.notify("agentbox spaces synced", ", ".join(done) or "nothing to change")
        return 0
    if action == "status":
        try:
            boxes = hub.boxes()
        except hub.HubError as e:
            herdr.notify("agentbox: hub unreachable", str(e)[:200])
            return 0
        body = ", ".join("%s %s" % (hub.label(b), b.get("status")) for b in boxes) or "no boxes"
        herdr.notify("Boxes", body)
        return 0
    box_id = focused_box()
    if not box_id:
        herdr.notify("Not a box pane", "focus a box's space first")
        return 0
    if action == "pause-focused":
        herdr.notify("Pausing %s" % box_id)
        detached("lifecycle", box_id, "pause")
    elif action == "resume-focused":
        herdr.notify("Resuming %s" % box_id, "its pane attaches when it is up")
        detached("lifecycle", box_id, "resume")
    elif action == "destroy-focused":
        if not confirmed(box_id):
            herdr.notify("Destroy %s?" % box_id, "run the action again within %ds to confirm" % CONFIRM_S)
            return 0
        herdr.notify("Destroying %s" % box_id, "its space closes when the hub drops it")
        detached("lifecycle", box_id, "destroy")
    return 0
