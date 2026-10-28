import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cycle  # noqa: E402
from cycle import EXPIRES, NEVER, current_token, previous_focus, rank, sort_spec, tokens_for, update_pane  # noqa: E402

EXP = "1793219084732"


def agent(status, expires=None):
    a = {"pane_id": "w1:p1", "agent_status": status}
    if expires:
        a["tokens"] = {EXPIRES: expires}
    return a


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
        self.assertEqual(tokens_for(agent("idle"), {}, "c", False)["cycle_seen"], NEVER)
        self.assertEqual(tokens_for(agent("idle"), {"seen": 1793206714435}, "c", False)["cycle_seen"], "1793206714435")
        self.assertLess(NEVER, "1793206714435")


    def test_current_flag_is_per_host_and_sorts_first(self):
        self.assertEqual(current_token("spencer-mbp.local"), "cycle_cur_spencer-mbp")
        self.assertEqual(current_token("nuc"), "cycle_cur_nuc")
        self.assertLessEqual(len(current_token("x" * 80)), 32)
        self.assertEqual(sort_spec("cycle_cur_nuc")[0]["field"], {"token": "cycle_cur_nuc"})
        self.assertEqual(tokens_for(agent("idle"), {}, "cycle_cur_nuc", True)["cycle_cur_nuc"], "1")
        self.assertIsNone(tokens_for(agent("idle"), {}, "cycle_cur_nuc", False)["cycle_cur_nuc"])

    def test_sort_order(self):
        keys = [s["field"]["token"] for s in sort_spec("cycle_cur_nuc")]
        self.assertEqual(keys, ["cycle_cur_nuc", "cycle_blocked", "cycle_cache_ack", EXPIRES, "cycle_rank", "cycle_seen"])
        self.assertTrue(all(s["order"] == "asc" for s in sort_spec("c")))

    def test_blocked_token_only_while_unvisited(self):
        e = update_pane(agent("blocked"), {"seen": 5}, now=10)
        self.assertEqual(tokens_for(agent("blocked"), e, "c", False)["cycle_blocked"], "0")
        e["seen"] = 11
        self.assertIsNone(tokens_for(agent("blocked"), e, "c", False)["cycle_blocked"])
        self.assertIsNone(tokens_for(agent("done"), {}, "c", False)["cycle_blocked"])

    def test_expiring_cache_is_acknowledged_by_being_on_it(self):
        a = agent("idle", EXP)
        e = update_pane(a, {}, now=10)
        self.assertEqual(tokens_for(a, e, "c", False)["cycle_cache_ack"], "0")
        e = update_pane(a, e, now=11, on_it=True)
        self.assertEqual(e["ack"], EXP)
        self.assertEqual(tokens_for(a, e, "c", False)["cycle_cache_ack"], "1")
        e = update_pane(a, e, now=12)  # stays acknowledged after you leave
        self.assertEqual(tokens_for(a, e, "c", False)["cycle_cache_ack"], "1")

    def test_acknowledgement_resets_with_a_new_expiry_or_none(self):
        e = update_pane(agent("idle", EXP), {}, now=10, on_it=True)
        later = agent("idle", "1793219999999")
        e2 = update_pane(later, e, now=11)
        self.assertNotIn("ack", e2)
        self.assertEqual(tokens_for(later, e2, "c", False)["cycle_cache_ack"], "0")
        self.assertNotIn("ack", update_pane(agent("idle"), e, now=11))

    def test_no_cache_guard_changes_nothing(self):
        t = tokens_for(agent("idle"), update_pane(agent("idle"), {}, now=10, on_it=True), "c", True)
        self.assertEqual(t["cycle_cache_ack"], "0")
        self.assertEqual(t["cycle_rank"], "2")

    def test_previous_focus_is_the_last_seen_pane(self):
        self.assertEqual(previous_focus({"a": {"seen": 5}, "b": {"seen": 9}, "c": {}}), "b")
        self.assertIsNone(previous_focus({"a": {}}))
        self.assertIsNone(previous_focus({}))


class SyncTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        patcher = mock.patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": d.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agents = []
        self.reports = {}

    def fake_call(self, method, params):
        if method == "agent.list":
            return {"agents": self.agents}
        if method == "pane.report_metadata":
            self.reports[params["pane_id"]] = params["tokens"]
        return {}

    def sync(self, **kw):
        with mock.patch.object(cycle, "call", self.fake_call):
            cycle.sync(**kw)

    def test_leaving_an_expiring_agent_acknowledges_it(self):
        a = {"pane_id": "a", "agent_status": "idle", "tokens": {}}
        b = {"pane_id": "b", "agent_status": "idle", "tokens": {}}
        self.agents = [a, b]
        self.sync(focused="a", now=100)
        a["tokens"] = {EXPIRES: EXP}  # its cache enters the window while you are on it
        self.sync(focused="b", now=200)
        self.assertEqual(self.reports["a"]["cycle_cache_ack"], "1")
        self.assertEqual(self.reports["b"]["cycle_cache_ack"], "0")

    def test_an_expiring_agent_you_were_not_on_stays_up(self):
        a = {"pane_id": "a", "agent_status": "idle", "tokens": {EXPIRES: EXP}}
        b = {"pane_id": "b", "agent_status": "idle", "tokens": {}}
        c = {"pane_id": "c", "agent_status": "idle", "tokens": {}}
        self.agents = [a, b, c]
        self.sync(focused="b", now=100)
        self.sync(focused="c", now=200)
        self.assertEqual(self.reports["a"]["cycle_cache_ack"], "0")


if __name__ == "__main__":
    unittest.main()
