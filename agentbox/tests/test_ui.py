import tests  # noqa: F401  first, so HOME is a throwaway dir
import io
import unittest

from herdr_agentbox import config, ui
from herdr_agentbox.progress import Progress

# Lines from a real hub create job (Daytona, agentbox 0.33.0), in order.
JOB_LOG = """\
leasing a push token for https://github.com/me/app
cloning https://github.com/me/app into /tmp/agentbox-hub-worker-7053bd79
provisioning daytona box from the clone
provisioning daytona sandbox
seeding /workspace from shallow git clone
installing claude (absent from this box image)
claude: staging host credentials
claude: credentials seeded
running in-box bootstrap (clone? + dockerd + ctl + vnc)
agentbox-ctl bootstrap: done — clone=false dockerd=up ctl=up vnc=up
seed: 1 uploaded, 0 unchanged
starting claude in app-1a2b with the seed prompt
""".splitlines()


class PhaseTest(unittest.TestCase):
    def test_real_log_walks_the_phases_in_order(self):
        seen = []
        for line in JOB_LOG:
            key = ui.phase_of(line)
            if key and (not seen or seen[-1] != key):
                seen.append(key)
        self.assertEqual(seen, ["clone", "provision", "seed", "boot", "agent"])

    def test_advance_closes_earlier_phases_and_skips_unseen(self):
        p = Progress("t", "s", ui.create_phases("claude", "docker:hub"), config.state_path("logs", "t.log"),
                     stream=io.StringIO())
        p.advance("queue")
        p.advance("provision")  # clone never logged
        states = {k: p.phases[k].state for k in p.order}
        self.assertEqual(states["queue"], "done")
        self.assertEqual(states["clone"], "skipped")
        self.assertEqual(states["provision"], "running")
        p.fail("boom")
        self.assertEqual(p.phases["provision"].state, "failed")
        p.finish()
        self.assertEqual(p.phases["agent"].state, "done")
        p.log.close()


class CardTest(unittest.TestCase):
    def test_card(self):
        b = {"id": "b1", "name": "t1", "repo": "me/app", "provider": "remote-docker", "status": "paused",
             "agentStatus": {"claude": {"state": "idle", "sessionTitle": "Fix login"}}}
        text = ui.card("t1", "paused", b, [("Enter", "resume"), ("q", "plain shell")], agent="claude",
                       footer="Resumed anywhere else, it attaches here on its own.")
        for part in ("t1", "paused", "me/app", "NUC", "claude", "Fix login", "Enter", "resume", "plain shell"):
            self.assertIn(part, text)
        self.assertEqual(ui.where({"provider": "daytona"}), "Daytona")


if __name__ == "__main__":
    unittest.main()
