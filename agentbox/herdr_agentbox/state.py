"""Small on-disk state shared by the daemon, the pane command and the picker.

- pending: box names a new-space picker is creating right now (name -> {workspace_id, at}).
  The daemon leaves those alone, so the space that asked for the box gets it.
- parked: a box whose pane the user dropped to a plain shell (`q`); the daemon does
  not restart its pane command until `agentbox-space box` runs there again.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Set

from . import config

PENDING_TTL_S = 20 * 60


def _pending_path() -> str:
    return config.state_path("pending.json")


def pending() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_pending_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if now - float(v.get("at", 0)) < PENDING_TTL_S}


def _write_pending(data: Dict[str, Dict[str, Any]]) -> None:
    tmp = _pending_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, _pending_path())


def add_pending(name: str, workspace_id: str) -> None:
    data = pending()
    data[name] = {"workspace_id": workspace_id, "at": time.time()}
    _write_pending(data)


def drop_pending(name: str) -> None:
    data = pending()
    if data.pop(name, None) is not None:
        _write_pending(data)


def pending_names() -> Set[str]:
    return set(pending())


def _parked_marker(box_id: str) -> str:
    return os.path.join(config.box_dir(box_id), ".parked")


def park(box_id: str) -> None:
    with open(_parked_marker(box_id), "w") as f:
        f.write(str(time.time()))


def unpark(box_id: str) -> None:
    try:
        os.unlink(_parked_marker(box_id))
    except OSError:
        pass


def parked() -> Set[str]:
    root = config.boxes_root()
    try:
        ids = os.listdir(root)
    except OSError:
        return set()
    return {i for i in ids if os.path.exists(os.path.join(root, i, ".parked"))}
