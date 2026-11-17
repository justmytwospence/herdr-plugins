"""What the daemon should change, from the hub's boxes and herdr's panes. Pure.

A box pane is any pane whose cwd is a box directory (config.box_dir); herdr
restores panes in their saved cwd, so this survives a herdr restart without any
pane ids. Per tick:

- a box with no pane gets a new space (unless a new-space picker is creating it);
- a box pane that is a bare shell (herdr restored it, or the pane command
  exited) gets `agentbox-space box` again, unless the user parked it;
- a space whose label is not its box's gets renamed;
- a space holding only panes of boxes the hub no longer lists, two ticks running,
  is closed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from . import hub

GONE_TICKS = 2


class Plan:
    def __init__(self) -> None:
        self.create: List[Dict[str, Any]] = []
        self.restart: List[Dict[str, str]] = []  # {"pane_id", "box_id"}
        self.rename: List[Dict[str, str]] = []   # {"workspace_id", "label"}
        self.close: List[str] = []               # workspace ids

    def empty(self) -> bool:
        return not (self.create or self.restart or self.rename or self.close)


def real_boxes(boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Boxes that exist (not a create still in flight)."""
    return [b for b in boxes if b.get("id") and not str(b["id"]).startswith("job:")]


def plan(
    boxes: List[Dict[str, Any]],
    panes: List[Dict[str, Any]],
    workspaces: List[Dict[str, Any]],
    box_of_dir: Callable[[Optional[str]], Optional[str]],
    is_shell: Callable[[str], bool],
    pending_names: Set[str],
    parked: Set[str],
    gone_ticks: Dict[str, int],
) -> Plan:
    """`gone_ticks` (box id -> consecutive ticks unlisted) is updated in place."""
    out = Plan()
    live = {b["id"]: b for b in real_boxes(boxes)}
    labels = {w.get("workspace_id"): w.get("label") for w in workspaces}

    panes_by_box: Dict[str, List[Dict[str, Any]]] = {}
    other_panes_in: Set[str] = set()
    for p in panes:
        box_id = box_of_dir(p.get("cwd"))
        if box_id:
            panes_by_box.setdefault(box_id, []).append(p)
        else:
            other_panes_in.add(p.get("workspace_id"))

    for box_id, b in live.items():
        gone_ticks.pop(box_id, None)
        mine = panes_by_box.get(box_id) or []
        if not mine:
            if b.get("name") not in pending_names and hub.label(b) not in pending_names:
                out.create.append(b)
            continue
        for p in mine:
            if box_id not in parked and is_shell(p["pane_id"]):
                out.restart.append({"pane_id": p["pane_id"], "box_id": box_id})
        first_ws = mine[0].get("workspace_id")
        if first_ws and labels.get(first_ws) != hub.label(b):
            out.rename.append({"workspace_id": first_ws, "label": hub.label(b)})

    live_ws = {p.get("workspace_id") for box_id, mine in panes_by_box.items() if box_id in live for p in mine}
    for box_id, mine in panes_by_box.items():
        if box_id in live:
            continue
        gone_ticks[box_id] = gone_ticks.get(box_id, 0) + 1
        if gone_ticks[box_id] < GONE_TICKS:
            continue
        for ws in {p.get("workspace_id") for p in mine}:
            # Never close a space that also holds something else.
            if ws and ws not in other_panes_in and ws not in live_ws and ws not in out.close:
                out.close.append(ws)
    for box_id in list(gone_ticks):
        if box_id not in panes_by_box and box_id not in live:
            gone_ticks.pop(box_id)
    return out
