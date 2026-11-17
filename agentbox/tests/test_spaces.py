import tests  # noqa: F401  first, so HOME is a throwaway dir
import random
import unittest

from herdr_agentbox import boxpane, config, hub, newspace, state


class BoxPaneTest(unittest.TestCase):
    def test_next_step(self):
        self.assertEqual(boxpane.next_step("running", False), "attach")
        self.assertEqual(boxpane.next_step("running", True), "detached")
        self.assertEqual(boxpane.next_step("paused", True), "paused")
        self.assertEqual(boxpane.next_step("stopped", False), "paused")
        self.assertEqual(boxpane.next_step("creating", False), "wait")
        self.assertEqual(boxpane.next_step("error", False), "error")
        self.assertEqual(boxpane.next_step(None, False), "gone")

    def test_box_agent_ignores_the_hubs_default(self):
        self.assertEqual(boxpane.box_agent({"id": "b1", "agent": "claude", "agentStatus": {"pi": {}}}), "pi")
        self.assertEqual(boxpane.box_agent({"id": "b2", "agent": "claude"}), "claude")
        import os
        with open(os.path.join(config.box_dir("b3"), ".agent"), "w") as f:
            f.write("codex")
        self.assertEqual(boxpane.box_agent({"id": "b3", "agent": "claude", "agentStatus": {}}), "codex")

    def test_attach_argv(self):
        self.assertEqual(boxpane.attach_argv("b1", "pi")[1:], ["pi", "attach", "--inline", "b1"])
        self.assertEqual(boxpane.attach_argv("b1", "shell")[1:], ["shell", "b1"])
        self.assertEqual(boxpane.attach_argv("b1", "openclaw")[1:], ["attach", "--inline", "b1"])


class NewSpaceTest(unittest.TestCase):
    def test_normalize_repo(self):
        for text in ("me/app", "https://github.com/me/app", "https://github.com/me/app.git",
                     "git@github.com:me/app.git", "ssh://git@github.com/me/app"):
            self.assertEqual(newspace.normalize_repo(text), "me/app", text)
        self.assertIsNone(newspace.normalize_repo("app"))
        self.assertIsNone(newspace.normalize_repo(""))

    def test_should_handle(self):
        user_space = {"workspace_id": "w1", "label": "3", "pane_count": 1, "tab_count": 1}
        self.assertTrue(newspace.should_handle(user_space, "/home/me", True))
        self.assertFalse(newspace.should_handle(user_space, "/home/me", False))
        self.assertFalse(newspace.should_handle(user_space, config.box_dir("b1"), True))
        self.assertFalse(newspace.should_handle(dict(user_space, worktree={"path": "x"}), "/home/me", True))
        self.assertFalse(newspace.should_handle(dict(user_space, pane_count=2), "/home/me", True))
        state.add_pending("app-1a2b", "w2")
        self.assertFalse(newspace.should_handle(dict(user_space, label="app-1a2b"), "/home/me", True))
        state.drop_pending("app-1a2b")

    def test_box_name(self):
        name = newspace.box_name("me/My_App.v2", [], random.Random(1))
        self.assertRegex(name, r"^my-app-v2-[0-9a-f]{4}$")
        self.assertNotEqual(newspace.box_name("me/app", [name], random.Random(1)), name)

    def test_known_repos_from_boxes(self):
        boxes = [{"originUrl": "git@github.com:me/old.git", "createdAt": 1},
                 {"originUrl": "https://github.com/me/new", "createdAt": 2},
                 {"repo": "me/new", "createdAt": 3}]
        newspace.gh_repos = lambda: ["me/new", "me/other"]
        self.assertEqual(newspace.known_repos(boxes), ["me/new", "me/old", "me/other"])


class StateTest(unittest.TestCase):
    def test_park_unpark(self):
        state.park("b5")
        self.assertIn("b5", state.parked())
        state.unpark("b5")
        self.assertNotIn("b5", state.parked())


class HubTest(unittest.TestCase):
    def test_repo_url_and_label(self):
        self.assertEqual(hub.repo_url("me/app"), "https://github.com/me/app")
        self.assertEqual(hub.repo_url("git@github.com:me/app.git"), "git@github.com:me/app.git")
        self.assertEqual(hub.label({"id": "b1", "name": "n", "displayName": "Pretty"}), "Pretty")
        self.assertEqual(hub.label({"id": "b1"}), "b1")


if __name__ == "__main__":
    unittest.main()
