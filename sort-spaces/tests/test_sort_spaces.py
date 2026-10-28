import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sort_spaces import plan_moves, target_order  # noqa: E402


def space(wid, label, number):
    return {"workspace_id": wid, "label": label, "number": number}


def apply(current, moves):
    order = list(current)
    for wid, index in moves:
        order.remove(wid)
        order.insert(index, wid)
    return order


class SortSpacesTest(unittest.TestCase):
    def test_case_insensitive_and_stable(self):
        spaces = [space("a", "dotfiles", 1), space("b", "Beta", 2), space("c", "alpha", 3), space("d", "dotfiles", 4)]
        self.assertEqual(target_order(spaces), ["c", "b", "a", "d"])
        self.assertEqual(target_order(spaces, reverse=True), ["d", "a", "b", "c"])

    def test_plan_reaches_target(self):
        current = ["e", "c", "a", "d", "b"]
        target = sorted(current)
        self.assertEqual(apply(current, plan_moves(current, target)), target)

    def test_sorted_needs_no_moves(self):
        self.assertEqual(plan_moves(["a", "b", "c"], ["a", "b", "c"]), [])

    def test_single_out_of_place(self):
        moves = plan_moves(["a", "b", "d", "c"], ["a", "b", "c", "d"])
        self.assertEqual(moves, [("c", 2)])


if __name__ == "__main__":
    unittest.main()
