import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cycle import NEVER, rank, tokens_for, update_pane  # noqa: E402


def agent(status):
    return {"pane_id": "w1:p1", "agent_status": status}


class CycleTest(unittest.TestCase):
    def test_blocked_counts_until_visited(self):
        e = update_pane(agent("blocked"), {"seen": 5}, now=10)
        self.assertEqual(e["blocked_since"], 10)
        self.assertEqual(rank(agent("blocked"), e), "0")
        e["seen"] = 11
        self.assertEqual(rank(agent("blocked"), e), "2")

    def test_blocked_since_is_kept_while_blocked_and_cleared_after(self):
        e = update_pane(agent("blocked"), {"blocked_since": 3}, now=10)
        self.assertEqual(e["blocked_since"], 3)
        self.assertNotIn("blocked_since", update_pane(agent("working"), e, now=12))

    def test_done_then_everything_else(self):
        self.assertEqual(rank(agent("done"), {}), "1")
        for s in ("working", "idle", "unknown"):
            self.assertEqual(rank(agent(s), {}), "2")

    def test_seen_token_sorts_never_first(self):
        self.assertEqual(tokens_for(agent("idle"), {})["cycle_seen"], NEVER)
        self.assertEqual(tokens_for(agent("idle"), {"seen": 1793206714435})["cycle_seen"], "1793206714435")
        self.assertLess(NEVER, "1793206714435")


if __name__ == "__main__":
    unittest.main()
