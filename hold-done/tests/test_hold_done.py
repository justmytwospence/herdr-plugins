import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tempfile  # noqa: E402
from unittest.mock import patch  # noqa: E402

import hold_done  # noqa: E402
from hold_done import desired_tokens, update_held  # noqa: E402


def agent(status, seq=None, pane="w1:p1"):
    a = {"pane_id": pane, "agent_status": status}
    if seq is not None:
        a["completion_seq"] = seq
    return a


class UpdateHeldTest(unittest.TestCase):
    def test_unseen_done_starts_a_hold(self):
        self.assertEqual(update_held(agent("done", 7), {}), 7)

    def test_seen_completion_without_a_hold_is_not_held(self):
        # Completions from before the plugin ran, or already seen, never hold.
        self.assertIsNone(update_held(agent("idle", 7), {}))

    def test_hold_survives_being_seen(self):
        self.assertEqual(update_held(agent("idle", 7), {"w1:p1": 7}), 7)

    def test_new_work_ends_the_hold(self):
        self.assertIsNone(update_held(agent("working", 7), {"w1:p1": 7}))
        self.assertIsNone(update_held(agent("idle", 9), {"w1:p1": 7}))


class DesiredTokensTest(unittest.TestCase):
    def test_ranks(self):
        self.assertEqual(desired_tokens(agent("blocked"), False), ("0", None))
        self.assertEqual(desired_tokens(agent("idle", 7), True), ("1", "done"))
        self.assertEqual(desired_tokens(agent("working"), False), ("2", None))
        self.assertEqual(desired_tokens(agent("idle", 7), False), ("3", None))
        self.assertEqual(desired_tokens(agent("unknown"), False), ("4", None))


class FocusTest(unittest.TestCase):
    def setUp(self):
        env = {"HERDR_PLUGIN_STATE_DIR": tempfile.mkdtemp()}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.released = []
        sync = patch.object(hold_done, "sync", side_effect=lambda release=(): self.released.append(set(release)))
        sync.start()
        self.addCleanup(sync.stop)

    def test_event_pane(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT_JSON": '{"event":"pane.focused","data":{"pane_id":"w1:p3"}}'}):
            self.assertEqual(hold_done.focused_pane_from_event(), "w1:p3")

    def test_arriving_releases_nothing_and_leaving_releases_the_previous_pane(self):
        hold_done.on_focus("w1:p1")
        hold_done.on_focus("w1:p1")
        hold_done.on_focus("w2:p1")
        self.assertEqual(self.released, [set(), set(), {"w1:p1"}])


if __name__ == "__main__":
    unittest.main()
