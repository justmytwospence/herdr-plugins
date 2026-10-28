#!/usr/bin/env python3
"""Sort herdr spaces (workspaces) alphabetically by label.

Usage: sort_spaces.py [--reverse] [--dry-run]

Talks to the server that spawned it through HERDR_SOCKET_PATH (plugin
processes get it set), falling back to the default socket. Stdlib only.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402



HerdrError = herdr_rpc.HerdrError


def call(method, params, timeout=5.0):
    return herdr_rpc.call(method, params, timeout=timeout, request_id="sort-spaces")


def list_workspaces():
    spaces = call("workspace.list", {}).get("workspaces", [])
    return sorted(spaces, key=lambda w: w["number"])


def sort_key(space):
    label = space.get("label") or ""
    # Case-insensitive, then case-sensitive, then current position (stable).
    return (label.casefold(), label, space["number"])


def target_order(spaces, reverse=False):
    ordered = sorted(spaces, key=sort_key)
    if reverse:
        ordered.reverse()
    return [w["workspace_id"] for w in ordered]


def plan_moves(current, target):
    """Moves (workspace_id, insert_index) turning `current` into `target`.

    Selection-sort style: place target[i] at index i, skipping spaces already
    in place, so an already-sorted sidebar makes no calls.
    """
    order = list(current)
    moves = []
    for i, wid in enumerate(target):
        if order[i] == wid:
            continue
        order.remove(wid)
        order.insert(i, wid)
        moves.append((wid, i))
    return moves


def main(argv):
    reverse = "--reverse" in argv
    dry_run = "--dry-run" in argv
    spaces = list_workspaces()
    current = [w["workspace_id"] for w in spaces]
    target = target_order(spaces, reverse)
    moves = plan_moves(current, target)
    labels = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in spaces}
    if not moves:
        print("Spaces already sorted.")
        return 0
    for wid, index in moves:
        print("%s %s -> %d" % ("would move" if dry_run else "move", labels[wid], index + 1))
        if not dry_run:
            call("workspace.move", {"workspace_id": wid, "insert_index": index})
    if not dry_run:
        final = [w["workspace_id"] for w in list_workspaces()]
        if final != target:
            print("warning: order after sorting differs from target", file=sys.stderr)
            return 1
    print("%s %d spaces (%d moves)." % ("Would sort" if dry_run else "Sorted", len(spaces), len(moves)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (HerdrError, OSError) as e:
        print("sort-spaces: %s" % e, file=sys.stderr)
        sys.exit(1)
