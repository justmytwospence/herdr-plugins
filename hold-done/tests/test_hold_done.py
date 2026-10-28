import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hold_done import desired_tokens  # noqa: E402


def agent(status, seq=None, pane="w1:p1"):
    a = {"pane_id": pane, "agent_status": status}
    if seq is not None:
        a["completion_seq"] = seq
    return a


class DesiredTokensTest(unittest.TestCase):
    def test_states(self):
        self.assertEqual(desired_tokens(agent("blocked"), {}), ("0", None))
        self.assertEqual(desired_tokens(agent("working", 5), {}), ("2", None))
        self.assertEqual(desired_tokens(agent("unknown"), {}), ("4", None))

    def test_unreleased_completion_is_held_whether_or_not_seen(self):
        self.assertEqual(desired_tokens(agent("idle", 7), {}), ("1", "done"))
        self.assertEqual(desired_tokens(agent("done", 7), {}), ("1", "done"))

    def test_released_completion_is_idle_until_a_newer_one(self):
        self.assertEqual(desired_tokens(agent("idle", 7), {"w1:p1": 7}), ("3", None))
        self.assertEqual(desired_tokens(agent("idle", 9), {"w1:p1": 7}), ("1", "done"))

    def test_idle_without_a_completion(self):
        self.assertEqual(desired_tokens(agent("idle"), {}), ("3", None))


if __name__ == "__main__":
    unittest.main()
