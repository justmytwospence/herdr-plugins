"""Repairs to the AgentBox CLI's own box records (~/.agentbox/state.json).

A machine that attaches to a box the hub created first "adopts" it: the CLI
writes a local record from the hub's registration. In agentbox 0.33.0 that
registration does not carry the Daytona sandbox class, and a record without one
reads as a `linux-vm`, so every exec into a `container` box is wrapped in
`sudo -u vscode`, which the container's sudoers refuses: the attach cannot stage
its script and loops on "box rebooting". A re-adoption keeps fields the record
already has, so setting the class once sticks.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import config


def state_path() -> str:
    return config.home(".agentbox", "state.json")


def _find(node: Any, box_id: str) -> Optional[dict]:
    if isinstance(node, dict):
        if node.get("id") == box_id and isinstance(node.get("cloud"), dict):
            return node
        for value in node.values():
            hit = _find(value, box_id)
            if hit is not None:
                return hit
    elif isinstance(node, list):
        for value in node:
            hit = _find(value, box_id)
            if hit is not None:
                return hit
    return None


def ensure_sandbox_class(box_id: str, sandbox_class: str, path: Optional[str] = None) -> bool:
    """Set a Daytona record's missing `cloud.sandboxClass`. True when it changed."""
    path = path or state_path()
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return False
    record = _find(state, box_id)
    if record is None or record.get("provider") != "daytona" or record["cloud"].get("sandboxClass"):
        return False
    record["cloud"]["sandboxClass"] = sandbox_class
    tmp = path + ".agentbox-space.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)
    return True


def has_record(box_id: str, path: Optional[str] = None) -> bool:
    try:
        with open(path or state_path()) as f:
            return _find(json.load(f), box_id) is not None
    except (OSError, ValueError):
        return False
