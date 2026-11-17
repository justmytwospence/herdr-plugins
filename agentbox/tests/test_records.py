import tests  # noqa: F401  first, so HOME is a throwaway dir
import json
import os
import unittest

from herdr_agentbox import config, records


class RecordsTest(unittest.TestCase):
    def write(self, boxes):
        path = config.state_path("agentbox-state.json")
        with open(path, "w") as f:
            json.dump({"version": 3, "boxes": boxes}, f)
        os.chmod(path, 0o600)
        return path

    def test_sets_a_missing_daytona_class_once(self):
        path = self.write([{"id": "b1", "provider": "daytona", "cloud": {"sandboxId": "s"}},
                           {"id": "b2", "provider": "remote-docker", "cloud": {}}])
        self.assertTrue(records.ensure_sandbox_class("b1", "container", path))
        self.assertFalse(records.ensure_sandbox_class("b1", "container", path))
        self.assertFalse(records.ensure_sandbox_class("b2", "container", path))
        self.assertFalse(records.ensure_sandbox_class("nope", "container", path))
        with open(path) as f:
            boxes = json.load(f)["boxes"]
        self.assertEqual(boxes[0]["cloud"]["sandboxClass"], "container")
        self.assertNotIn("sandboxClass", boxes[1]["cloud"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_keeps_a_recorded_class(self):
        path = self.write([{"id": "b1", "provider": "daytona", "cloud": {"sandboxClass": "linux-vm"}}])
        self.assertFalse(records.ensure_sandbox_class("b1", "container", path))

    def test_missing_state_file(self):
        self.assertFalse(records.ensure_sandbox_class("b1", "container", "/nonexistent/state.json"))


if __name__ == "__main__":
    unittest.main()
