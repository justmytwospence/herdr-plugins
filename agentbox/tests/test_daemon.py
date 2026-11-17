import tests  # noqa: F401  first, so HOME is a throwaway dir
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

from herdr_agentbox import config, daemon


def _write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.link = config.home(".local", "bin", "agentbox-space")
        if os.path.lexists(self.link):
            os.unlink(self.link)

    def test_replaces_a_symlink_and_is_idempotent(self):
        os.makedirs(os.path.dirname(self.link), exist_ok=True)
        os.symlink("/gone/generation/agentbox-space.py", self.link)
        daemon.link_command()
        self.assertFalse(os.path.islink(self.link))
        self.assertTrue(os.access(self.link, os.X_OK))
        with open(self.link) as f:
            self.assertEqual(f.read(), daemon.wrapper_script())
        before = os.stat(self.link).st_mtime_ns
        daemon.link_command()
        self.assertEqual(os.stat(self.link).st_mtime_ns, before)

    def test_rewrites_an_old_wrapper_but_not_a_users_file(self):
        _write(self.link, "#!/bin/sh\n%s: old\n" % daemon.WRAPPER_MARK, 0o755)
        daemon.link_command()
        with open(self.link) as f:
            self.assertEqual(f.read(), daemon.wrapper_script())
        _write(self.link, "#!/bin/sh\necho mine\n", 0o755)
        daemon.link_command()
        with open(self.link) as f:
            self.assertEqual(f.read(), "#!/bin/sh\necho mine\n")

    def test_runs_the_root_herdr_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "generation", "checkout")
            _write(os.path.join(root, "agentbox-space.py"), "import sys\nprint('ran', *sys.argv[1:])\n")
            listing = {"result": {"plugins": [{"plugin_root": root}]}}
            fake = os.path.join(d, "herdr")
            _write(fake, "#!/bin/sh\necho '%s'\n" % json.dumps(listing), 0o755)
            daemon.link_command()
            out = subprocess.run([self.link, "status", "x"], capture_output=True, text=True,
                                 env=dict(os.environ, HERDR_BIN_PATH=fake), timeout=20)
            self.assertEqual(out.stdout.strip(), "ran status x", out.stderr)

    def test_falls_back_to_this_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "herdr")
            _write(fake, "#!/bin/sh\necho '{\"result\": {\"plugins\": []}}'\n", 0o755)
            daemon.link_command()
            out = subprocess.run([self.link, "--help"], capture_output=True, text=True,
                                 env=dict(os.environ, HERDR_BIN_PATH=fake), timeout=20)
            self.assertIn("agentbox-space", out.stdout + out.stderr)


class StaleDaemonTest(unittest.TestCase):
    def _spawn(self, script):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", script])
        threading.Thread(target=proc.wait, daemon=True).start()
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        with open(config.state_path("daemon.pid"), "w") as f:
            f.write(str(proc.pid))
        return proc

    def test_keeps_a_daemon_from_this_checkout(self):
        proc = self._spawn(os.path.join(config.PLUGIN_ROOT, "agentbox-space.py"))
        self.assertEqual(daemon.daemon_script(proc.pid), os.path.join(config.PLUGIN_ROOT, "agentbox-space.py"))
        self.assertFalse(daemon.stop_stale(proc.pid))
        self.assertIsNone(proc.poll())

    def test_stops_a_daemon_from_another_generation(self):
        proc = self._spawn("/gone/generation/checkout/agentbox-space.py")
        self.assertTrue(daemon.stop_stale(proc.pid))
        proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
