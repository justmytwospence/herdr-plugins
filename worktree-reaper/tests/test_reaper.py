import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import reaper as r


def sh(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", *args], cwd=cwd, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fake_git(table):
    """A git runner answering by the first matching subcommand: table[(cmd, ...)] -> (code, out)."""
    def run(args, cwd, timeout=60):
        for prefix, (code, out) in table.items():
            if tuple(args[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(args, code, out, "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")
    return run


PORCELAIN = """worktree /r
HEAD aaa
branch refs/heads/main

worktree /r/.worktrees/a
HEAD bbb
branch refs/heads/a
locked pi-worktree pid 12

worktree /r/.worktrees/b
HEAD ccc
branch refs/heads/b

worktree /r/.worktrees/subagent-x
HEAD ddd
branch refs/heads/subagent-x

worktree /r/.worktrees/d
HEAD eee
detached

worktree /r/.worktrees/p
HEAD fff
branch refs/heads/p
prunable gitdir file points to non-existent location

worktree /elsewhere/e
HEAD 111
branch refs/heads/e
"""


class Parsing(unittest.TestCase):
    def test_parse_worktrees(self):
        trees = r.parse_worktrees(PORCELAIN)
        self.assertEqual([t["path"] for t in trees], [
            "/r", "/r/.worktrees/a", "/r/.worktrees/b", "/r/.worktrees/subagent-x",
            "/r/.worktrees/d", "/r/.worktrees/p", "/elsewhere/e"])
        self.assertEqual(trees[0]["branch"], "main")
        self.assertTrue(trees[1]["locked"])
        self.assertTrue(trees[4]["detached"])
        self.assertIsNone(trees[4]["branch"])
        self.assertTrue(trees[5]["prunable"])

    def test_managed_excludes_main_locked_subagent_detached_prunable_and_foreign(self):
        trees = r.parse_worktrees(PORCELAIN)
        self.assertEqual([t["path"] for t in trees if r.managed("/r", t)], ["/r/.worktrees/b"])

    def test_remaining_and_token_text(self):
        self.assertEqual(r.remaining(0), "now")
        self.assertEqual(r.remaining(-5), "now")
        self.assertEqual(r.remaining(90), "2m")
        self.assertEqual(r.remaining(3 * 3600), "3h")
        self.assertEqual(r.token_text("dev", 3 * 3600), "\u2713 dev \u00b7 3h")
        self.assertEqual(r.short("origin/dev"), "dev")
        self.assertEqual(r.short("main"), "main")


class Landed(unittest.TestCase):
    def test_default_base_falls_back_to_origin_main_then_none(self):
        head = fake_git({("symbolic-ref",): (0, "origin/dev\n")})
        self.assertEqual(r.default_base("/r", head), "origin/dev")
        main = fake_git({("symbolic-ref",): (1, ""), ("rev-parse",): (0, "abc\n")})
        self.assertEqual(r.default_base("/r", main), "origin/main")
        none = fake_git({("symbolic-ref",): (1, ""), ("rev-parse",): (1, "")})
        self.assertIsNone(r.default_base("/r", none))

    def test_contained_in_base_needs_no_gh(self):
        run = fake_git({("merge-base",): (0, "")})
        calls = []
        result = r.landed("/r", "x", "abc", "origin/dev", {}, 0, run, lambda *a: calls.append(a))
        self.assertEqual(result, "origin/dev")
        self.assertEqual(calls, [])

    def test_merged_pull_request_at_the_tip_and_its_cache(self):
        run = fake_git({("merge-base",): (1, "")})
        calls = []

        def gh(root, branch):
            calls.append(branch)
            return ["zzz", "abc"]

        cache = {}
        self.assertEqual(r.landed("/r", "x", "abc", "origin/dev", cache, 0, run, gh), "a merged pull request")
        self.assertEqual(r.landed("/r", "x", "abc", "origin/dev", cache, 60, run, gh), "a merged pull request")
        self.assertEqual(calls, ["x"])
        r.landed("/r", "x", "abc", "origin/dev", cache, r.GH_CACHE + 1, run, gh)
        self.assertEqual(calls, ["x", "x"])

    def test_not_landed_when_the_pull_request_head_differs_or_gh_cannot_tell(self):
        run = fake_git({("merge-base",): (1, "")})
        self.assertIsNone(r.landed("/r", "x", "abc", "origin/dev", {}, 0, run, lambda *a: ["other"]))
        self.assertIsNone(r.landed("/r", "x", "abc", "origin/dev", {}, 0, run, lambda *a: None))

    def test_has_work_needs_more_than_the_creation_entry(self):
        one = fake_git({("reflog",): (0, "branch: Created from origin/dev\n")})
        two = fake_git({("reflog",): (0, "commit: x\nbranch: Created from origin/dev\n")})
        none = fake_git({("reflog",): (0, "")})
        broken = fake_git({("reflog",): (128, "")})
        self.assertFalse(r.has_work("/r", "x", one))
        self.assertTrue(r.has_work("/r", "x", two))
        self.assertFalse(r.has_work("/r", "x", none))
        self.assertFalse(r.has_work("/r", "x", broken))

    def test_git_never_raises(self):
        with patch.object(r.subprocess, "run", side_effect=OSError("no git")):
            result = r.git(["status"], "/tmp")
        self.assertEqual(result.returncode, 1)


class Cycle(unittest.TestCase):
    """Real git: a bare origin whose default branch is dev, and a root with four worktrees."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.origin = base / "origin.git"
        self.root = base / "repo"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.root.mkdir()
        sh(self.root, "init", "-q", "-b", "main")
        (self.root / "a.txt").write_text("a\n")
        sh(self.root, "add", "-A")
        sh(self.root, "commit", "-qm", "init")
        sh(self.root, "remote", "add", "origin", str(self.origin))
        sh(self.root, "push", "-q", "origin", "main", "main:dev")
        sh(self.root, "fetch", "-q", "origin")
        sh(self.root, "remote", "set-head", "origin", "dev")
        for name in ("fresh", "landed", "wip", "dirty"):
            sh(self.root, "worktree", "add", "-q", "--no-track", "-b", name,
               str(self.root / ".worktrees" / name), "origin/dev")
        for name in ("landed", "wip", "dirty"):
            self.commit(name)
        sh(self.root, "push", "-q", "origin", "landed:dev")
        sh(self.root, "fetch", "-q", "origin")
        sh(self.root / ".worktrees" / "dirty", "merge", "-q", "--no-edit", "origin/dev")
        sh(self.root, "push", "-q", "origin", "dirty:dev")
        (self.root / ".worktrees" / "dirty" / "untracked.txt").write_text("x")
        self.calls = []
        self.agents = [{"workspace_id": "wL", "agent_status": "idle"}]
        self.focused = set()
        self.removed = []
        self.state = {}
        self.clock = 1000.0

    def commit(self, name):
        tree = self.root / ".worktrees" / name
        (tree / f"{name}.txt").write_text(name + os.urandom(4).hex())
        sh(tree, "add", "-A")
        sh(tree, "commit", "-qm", name)

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "session.snapshot":
            return {"snapshot": {"workspaces": [
                {"workspace_id": f"w{name[0].upper()}", "focused": f"w{name[0].upper()}" in self.focused,
                 "worktree": {"checkout_path": str(self.root / ".worktrees" / name),
                              "repo_root": str(self.root), "is_linked_worktree": True}}
                for name in ("landed", "dirty", "fresh", "wip")] + [
                {"workspace_id": "wMAIN", "focused": False,
                 "worktree": {"checkout_path": str(self.root), "repo_root": str(self.root),
                              "is_linked_worktree": False}}]}}
        if method == "agent.list":
            return {"agents": self.agents}
        if method == "worktree.remove":
            name = {"wL": "landed", "wD": "dirty", "wF": "fresh", "wW": "wip"}[params["workspace_id"]]
            sh(self.root, "worktree", "remove", str(self.root / ".worktrees" / name))
            self.removed.append(name)
        return {}

    def run_cycle(self, at=0, **kwargs):
        self.clock = 1000.0 + at
        return r.cycle(self.state, call=self.call, now=lambda: self.clock,
                       gh=lambda *a: None, **kwargs)

    def tokens(self):
        return {p["workspace_id"]: p["tokens"][r.TOKEN] for m, p in self.calls
                if m == "workspace.report_metadata"}

    def branches(self):
        out = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=self.root,
                             capture_output=True, text=True, check=True).stdout.split()
        return set(out)

    def test_grace_periods_and_what_is_never_pending(self):
        self.assertEqual(self.run_cycle(0), [])
        self.assertEqual(set(self.state["pending"]), {
            str(self.root / ".worktrees" / "landed"), str(self.root / ".worktrees" / "dirty")})
        self.assertEqual(self.tokens()["wL"], "\u2713 dev \u00b7 4h")
        self.assertNotIn("wF", self.tokens())
        self.assertNotIn("wW", self.tokens())
        # An idle agent is in wL: the long grace applies, not the 2 minutes.
        self.assertEqual(self.run_cycle(r.GRACE_EMPTY + 1), [])
        self.assertEqual(self.removed, [])
        lines = self.run_cycle(r.GRACE_AGENT + 1)
        self.assertEqual(self.removed, ["landed"])
        self.assertEqual(len(lines), 1)
        self.assertIn("repo/landed", lines[0])
        self.assertNotIn("landed", self.branches())
        # Dirty, fresh and unlanded work stay.
        self.assertEqual({"dirty", "fresh", "wip"} & self.branches(), {"dirty", "fresh", "wip"})
        self.assertTrue(any(m == "notification.show" for m, _ in self.calls))
        self.assertNotIn(str(self.root / ".worktrees" / "landed"), self.state["pending"])

    def test_a_worktree_without_an_agent_uses_the_short_grace(self):
        self.agents = []
        self.assertEqual(self.run_cycle(0), [])
        self.assertEqual(self.run_cycle(r.GRACE_EMPTY - 1), [])
        self.assertEqual(len(self.run_cycle(r.GRACE_EMPTY + 1)), 1)
        self.assertEqual(self.removed, ["landed"])

    def test_force_removes_at_once_but_a_busy_agent_blocks_it(self):
        self.agents = [{"workspace_id": "wL", "agent_status": "working"}]
        self.assertEqual(self.run_cycle(0, force=True), [])
        self.assertEqual(self.removed, [])
        self.agents = [{"workspace_id": "wL", "agent_status": "blocked"}]
        self.assertEqual(self.run_cycle(1, force=True), [])
        self.agents = [{"workspace_id": "wL", "agent_status": "idle"}]
        lines = self.run_cycle(2, force=True)
        self.assertEqual(self.removed, ["landed"])
        self.assertEqual(len(lines), 1)

    def test_a_focused_space_waits_unless_forced(self):
        self.agents = []
        self.focused = {"wL"}
        self.assertEqual(self.run_cycle(0), [])
        self.assertEqual(self.run_cycle(r.GRACE_EMPTY + 1), [])
        self.assertEqual(len(self.run_cycle(r.GRACE_EMPTY + 2, force=True)), 1)

    def test_a_new_commit_before_grace_resets_it(self):
        self.agents = []
        self.run_cycle(0)
        path = str(self.root / ".worktrees" / "landed")
        first = self.state["pending"][path]["since"]
        self.commit("landed")
        # Unpushed again: no longer landed, so no longer pending.
        self.run_cycle(60)
        self.assertNotIn(path, self.state["pending"])
        self.assertEqual(self.calls[-1][0], "workspace.report_metadata")
        self.assertIsNone(self.calls[-1][1]["tokens"][r.TOKEN])
        sh(self.root, "fetch", "-q", "origin")
        sh(self.root / ".worktrees" / "landed", "merge", "-q", "--no-edit", "origin/dev")
        sh(self.root, "push", "-q", "origin", "landed:dev")
        self.run_cycle(120)
        self.assertGreater(self.state["pending"][path]["since"], first)

    def test_an_open_space_is_removed_through_herdr_and_none_through_git(self):
        self.agents = []

        def without_space(method, params=None):
            result = self.call(method, params)
            if method == "session.snapshot":
                result["snapshot"]["workspaces"] = [
                    w for w in result["snapshot"]["workspaces"] if w["workspace_id"] != "wL"]
                result["snapshot"]["workspaces"][0]["worktree"]["repo_root"] = str(self.root)
            return result

        self.clock = 1000.0
        r.cycle(self.state, call=without_space, now=lambda: self.clock, gh=lambda *a: None)
        self.clock += r.GRACE_EMPTY + 1
        lines = r.cycle(self.state, call=without_space, now=lambda: self.clock, gh=lambda *a: None)
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.removed, [])  # worktree.remove was not needed: git removed it
        self.assertFalse((self.root / ".worktrees" / "landed").exists())
        self.assertNotIn("landed", self.branches())

    def test_fetch_runs_at_most_every_five_minutes(self):
        fetches = []

        def counting(args, cwd, timeout=60):
            if args[0] == "fetch":
                fetches.append(1)
            return r.git(args, cwd, timeout)

        for at in (0, 60, r.FETCH_EVERY - 1, r.FETCH_EVERY + 1):
            self.clock = 1000.0 + at
            r.cycle(self.state, call=self.call, run=counting, now=lambda: self.clock, gh=lambda *a: None)
        self.assertEqual(len(fetches), 2)

    def test_no_default_base_skips_the_repository(self):
        sh(self.root, "remote", "set-head", "origin", "-d")
        sh(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.run_cycle(0, force=True), [])
        self.assertEqual(self.state["pending"], {})


class DaemonLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_the_daemon_exits_when_another_holds_the_lock(self):
        with (self.root / "lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(r, "locked_cycle") as cycle:
                r.daemon(self.root)
                cycle.assert_not_called()

    def test_a_disabled_marker_stops_the_daemon_before_any_pass(self):
        (self.root / "disabled").touch()
        with patch.object(r, "locked_cycle") as cycle:
            r.daemon(self.root)
            cycle.assert_not_called()

    def test_now_waits_for_a_running_pass_and_gives_up(self):
        with (self.root / "cycle.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIsNone(r.locked_cycle(self.root, force=True, wait=0.2))

    def test_state_round_trips_and_survives_garbage(self):
        r.save_state(self.root, {"fetched": {"/r": 1.0}, "pending": {"/r/w": {"sha": "a"}}, "gh_cache": {"k": 1}})
        self.assertEqual(r.load_state(self.root), {"fetched": {"/r": 1.0}, "pending": {"/r/w": {"sha": "a"}}})
        (self.root / "state.json").write_text("{nope")
        self.assertEqual(r.load_state(self.root), {"fetched": {}, "pending": {}})

    def test_config_overrides_the_grace_periods(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(self.root)}):
            self.assertEqual(r.load_config(), {})
            (self.root / "config.json").write_text('{"grace_empty_s": 5, "grace_agent_s": 9}')
            self.assertEqual(r.load_config(), {"grace_empty_s": 5, "grace_agent_s": 9})


if __name__ == "__main__":
    unittest.main()
