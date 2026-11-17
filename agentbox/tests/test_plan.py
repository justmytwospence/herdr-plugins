import tests  # noqa: F401  first, so HOME is a throwaway dir
import unittest

from herdr_agentbox import config, plan


def box(i, name=None, status="running"):
    return {"id": i, "name": name or i, "status": status, "agent": "claude"}


def pane(pane_id, ws, cwd):
    return {"pane_id": pane_id, "workspace_id": ws, "cwd": cwd}


def run(boxes, panes, workspaces=(), shells=(), pending=(), parked=(), gone=None):
    return plan.plan(list(boxes), list(panes), list(workspaces), config.box_of_dir,
                     lambda p: p in shells, set(pending), set(parked), {} if gone is None else gone)


class PlanTest(unittest.TestCase):
    def test_new_box_gets_a_space(self):
        out = run([box("b1"), box("job:9", status="creating")], [])
        self.assertEqual([b["id"] for b in out.create], ["b1"])

    def test_pending_name_is_left_to_its_picker(self):
        out = run([box("b1", "fix-1234")], [], pending=["fix-1234"])
        self.assertEqual(out.create, [])

    def test_restored_shell_is_restarted_unless_parked(self):
        p = pane("w1:p1", "w1", config.box_dir("b1"))
        ws = [{"workspace_id": "w1", "label": "b1"}]
        self.assertEqual(run([box("b1")], [p], ws, shells=["w1:p1"]).restart,
                         [{"pane_id": "w1:p1", "box_id": "b1"}])
        self.assertEqual(run([box("b1")], [p], ws, shells=["w1:p1"], parked=["b1"]).restart, [])
        self.assertEqual(run([box("b1")], [p], ws, shells=[]).restart, [])

    def test_space_is_renamed_to_its_box(self):
        p = pane("w1:p1", "w1", config.box_dir("b1"))
        out = run([box("b1", "fix-login")], [p], [{"workspace_id": "w1", "label": "1"}])
        self.assertEqual(out.rename, [{"workspace_id": "w1", "label": "fix-login"}])
        self.assertTrue(run([box("b1", "fix-login")], [p], [{"workspace_id": "w1", "label": "fix-login"}]).empty())

    def test_gone_box_space_closes_after_two_ticks(self):
        p = pane("w1:p1", "w1", config.box_dir("b1"))
        gone = {}
        self.assertEqual(run([], [p], gone=gone).close, [])
        self.assertEqual(run([], [p], gone=gone).close, ["w1"])

    def test_never_closes_a_space_holding_anything_else(self):
        gone = {"b1": 5}
        panes = [pane("w1:p1", "w1", config.box_dir("b1")), pane("w1:p2", "w1", "/home/me")]
        self.assertEqual(run([], panes, gone=gone).close, [])
        panes = [pane("w1:p1", "w1", config.box_dir("b1")), pane("w1:p2", "w1", config.box_dir("b2"))]
        self.assertEqual(run([box("b2")], panes, gone=gone).close, [])

    def test_box_back_resets_its_gone_count(self):
        p = pane("w1:p1", "w1", config.box_dir("b1"))
        gone = {"b1": 1}
        run([box("b1")], [p], gone=gone)
        self.assertEqual(gone, {})

    def test_other_panes_are_ignored(self):
        self.assertTrue(run([], [pane("w9:p1", "w9", "/home/me/Projects/x")]).empty())


class ConfigTest(unittest.TestCase):
    def test_box_of_dir(self):
        self.assertEqual(config.box_of_dir(config.box_dir("b7")), "b7")
        self.assertIsNone(config.box_of_dir(config.boxes_root()))
        self.assertIsNone(config.box_of_dir("/tmp"))
        self.assertIsNone(config.box_of_dir(None))

    def test_control_plane_url(self):
        path = config.state_path("cfg.yaml")
        with open(path, "w") as f:
            f.write("box:\n  controlPlaneUrl: wrong\nrelay:\n  port: 1\n  controlPlaneUrl: https://hub.example/\n")
        self.assertEqual(config.control_plane_url(path), "https://hub.example")
        with open(path, "w") as f:
            f.write("box:\n  provider: docker\n")
        self.assertEqual(config.control_plane_url(path), "")


if __name__ == "__main__":
    unittest.main()
