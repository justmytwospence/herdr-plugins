"""`agentbox-space <command>`."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

USAGE = """agentbox-space: one herdr space per AgentBox box

  box [<id>]                  a box's pane: attach while running, Enter resumes a paused box
  new-space [--workspace ID]  ask for repo/agent/where, create a box, print shell code for the pane
  daemon [--ensure]           keep a space per box (the plugin's startup hook runs --ensure)
  sync                        one daemon pass now, printing what it changed
  status                      boxes on the hub and their spaces
  lifecycle <id> <action>     start | pause | resume | stop | destroy
  on-workspace-created        (herdr event hook)
  action <id>                 (herdr plugin action)
"""


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "box":
        from . import boxpane
        return boxpane.main(rest[0] if rest else None)
    if cmd == "new-space":
        from . import newspace
        p = argparse.ArgumentParser(prog="agentbox-space new-space")
        p.add_argument("--workspace")
        return newspace.main(p.parse_args(rest).workspace)
    if cmd == "on-workspace-created":
        from . import newspace
        return newspace.on_workspace_created()
    if cmd == "daemon":
        from . import daemon
        return daemon.ensure() if "--ensure" in rest else daemon.main()
    if cmd == "sync":
        from . import daemon
        for line in daemon.tick(None, {}) or ["nothing to change"]:
            print(line)
        return 0
    if cmd == "status":
        from . import config, daemon, herdr, hub
        panes = herdr.call("pane.list", {}).get("panes") or []
        spaces = {config.box_of_dir(p.get("cwd")): p.get("workspace_id") for p in panes}
        for b in hub.boxes():
            print("%-28s %-9s %-12s %-14s space %s" % (hub.label(b), b.get("status"), b.get("agent"),
                                                       b.get("provider"), spaces.get(b.get("id"), "-")))
        print("daemon: %s" % ("pid %d" % daemon.running_pid() if daemon.running_pid() else "not running"))
        return 0
    if cmd == "lifecycle" and len(rest) == 2:
        from . import actions
        return actions.lifecycle(rest[0], rest[1])
    if cmd == "action" and rest:
        from . import actions
        return actions.run(rest[0])
    print(USAGE, file=sys.stderr)
    return 2
