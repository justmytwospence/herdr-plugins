import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import git_status as g


def tokens(**values):
    state = {**g.empty_tokens(), **values}
    if "git_summary" not in values:
        state["git_summary"] = g.summary(state)
    return state


HEADER = (b"# branch.oid " + b"a" * 40 +
          b"\0# branch.head main\0# branch.upstream origin/main\0# branch.ab +0 -0\0")


def ordinary(path, status=b"MM"):
    return b"1 " + status + b" N... 100644 100644 100644 abc abc " + path + b"\0"


def conflict(path, status=b"UU"):
    return b"u " + status + b" N... 100644 100644 100644 100644 abc abc abc " + path + b"\0"


class Parsing(unittest.TestCase):
    def test_clean_and_ignored(self):
        self.assertEqual(g.parse_status(HEADER), tokens())
        self.assertEqual(g.parse_status(HEADER + b"! ignored\0"), tokens())
        self.assertEqual(set(g.empty_tokens()), {
            "git_conflict", "git_changes", "git_sync", "git_ahead", "git_behind",
            "git_tracking", "git_error", "git_dirty", "git_summary", "git_child_summary"})

    def test_tokens_are_symbolic_and_legacy_dirty_is_null(self):
        states = [g.parse_status(data) for data in [
            HEADER, HEADER + ordinary(b"file") + conflict(b"other"),
            HEADER.replace(b"# branch.ab +0 -0\0", b"# branch.ab +2 -3\0"),
            b"# branch.oid (initial)\0# branch.head main\0",
            HEADER.replace(b"# branch.ab +0 -0\0", b"")]]
        with patch.object(g.subprocess, "run", side_effect=OSError("missing git")):
            states.append(g.git_tokens("/repo"))
        visible = set()
        for state in states:
            self.assertIsNone(state["git_dirty"])
            for value in state.values():
                if value is not None:
                    for mark in value.split():
                        self.assertRegex(mark, r"^(?:[✖*]|[↑↓][1-9][0-9]*|↕[—?]|!)$")
                    visible.add(value)
        self.assertTrue({"*", "✖", "↑2", "↓3", "↑2 ↓3", "↕—", "↕?", "!"} <= visible)

    def test_each_change_kind(self):
        for status in [b"M.", b".M", b"A.", b".D", b"MM", b".T"]:
            with self.subTest(status=status):
                self.assertEqual(g.parse_status(HEADER + ordinary(b"file", status)),
                                 tokens(git_changes="*"))

    def test_unique_paths_and_conflict_counts(self):
        data = ordinary(b"file") + ordinary(b"file") + b"? file\0" + conflict(b"file")
        self.assertEqual(g.parse_status(HEADER + data), tokens(git_changes="*", git_conflict="✖"))
        data = b"".join(conflict(str(i).encode(), s) for i, s in enumerate(
            [b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"]))
        self.assertEqual(g.parse_status(HEADER + data), tokens(git_changes="*", git_conflict="✖"))

    def test_rename_and_copy_skip_original_path(self):
        for status in [b"R.", b"C."]:
            data = b"2 " + status + b" N... 100644 100644 100644 abc abc R100 new\nname\0"
            data += b"u UU misleading old path\0? new\nfile\0"
            self.assertEqual(g.parse_status(HEADER + data), tokens(git_changes="*"))

    def test_branch_states(self):
        self.assertEqual(g.parse_status(b"# branch.oid (initial)\0# branch.head main\0"),
                         tokens(git_tracking="↕—"))
        detached = b"# branch.oid " + b"a" * 40 + b"\0# branch.head (detached)\0"
        self.assertEqual(g.parse_status(detached), tokens())
        self.assertEqual(g.parse_status(detached + ordinary(b"file")),
                         tokens(git_changes="*"))
        attached = b"# branch.oid (initial)\0# branch.head main\0"
        self.assertEqual(g.parse_status(attached + ordinary(b"file")),
                         tokens(git_changes="*", git_tracking="↕—"))
        for counts, sync, ahead, behind in [
                (b"+0 -0", None, None, None), (b"+2 -0", "↑2", "↑2", None),
                (b"+0 -3", "↓3", None, "↓3"), (b"+2 -3", "↑2 ↓3", "↑2", "↓3")]:
            with self.subTest(counts=counts):
                self.assertEqual(g.parse_status(HEADER.replace(b"# branch.ab +0 -0\0", b"# branch.ab " + counts + b"\0")),
                                 tokens(git_sync=sync, git_ahead=ahead, git_behind=behind))


    def test_missing_upstream_is_not_in_sync(self):
        missing = HEADER.replace(b"# branch.ab +0 -0\0", b"")
        self.assertEqual(g.parse_status(missing), tokens(git_tracking="↕?"))
        self.assertEqual(g.parse_status(missing + ordinary(b"file")),
                         tokens(git_changes="*", git_tracking="↕?"))
        unborn = missing.replace(b"a" * 40, b"(initial)")
        self.assertEqual(g.parse_status(unborn), tokens())
        detached = missing.replace(b"# branch.head main", b"# branch.head (detached)")
        self.assertEqual(g.parse_status(detached), tokens())
        detached_unconfigured = detached.replace(b"# branch.upstream origin/main\0", b"")
        self.assertEqual(g.parse_status(detached_unconfigured), tokens())


class Summary(unittest.TestCase):
    def test_single_summary_order_and_no_tracking_or_counts(self):
        state = g.parse_status(HEADER.replace(b"+0 -0", b"+2 -1") +
                               ordinary(b"file") + conflict(b"other"))
        self.assertEqual(state["git_summary"], "* ↑2 ↓1 !")
        self.assertIsNone(state["git_child_summary"])
        state["git_error"] = "!"
        self.assertEqual(g.summary(state), "* ↑2 ↓1 !")
        self.assertIsNone(g.summary(tokens(git_tracking="↕—")))
        self.assertIsNone(g.summary(tokens(git_tracking="↕?")))


class Reporting(unittest.TestCase):
    def test_main_thread_routes_from_snapshot_flag_and_clears_old_route(self):
        main_thread = threading.get_ident()
        workspaces = [
            {"workspace_id": "child", "worktree": {
                "checkout_path": "/child", "is_linked_worktree": True}},
            {"workspace_id": "ordinary", "worktree": {
                "checkout_path": "/ordinary", "is_linked_worktree": False}},
            {"workspace_id": "no-flag", "worktree": {"checkout_path": "/other"}},
            {"workspace_id": "plain"}]
        reports = []
        def call(method, params=None):
            self.assertEqual(threading.get_ident(), main_thread)
            if method == "session.snapshot":
                return {"snapshot": {"workspaces": workspaces, "panes": []}}
            reports.append(params)
        def read(cwd):
            self.assertNotEqual(threading.get_ident(), main_thread)
            return tokens(git_changes="*", git_ahead="↑2", git_behind="↓1",
                          git_conflict="✖", git_error="!")
        result = g.refresh(call, read)
        self.assertEqual(result["child"]["git_child_summary"], "* ↑2 ↓1 !")
        self.assertIsNone(result["child"]["git_summary"])
        for wid in ["ordinary", "no-flag", "plain"]:
            self.assertEqual(result[wid]["git_summary"], "* ↑2 ↓1 !")
            self.assertIsNone(result[wid]["git_child_summary"])
        workspaces[0]["worktree"]["is_linked_worktree"] = False
        workspaces[1]["worktree"]["is_linked_worktree"] = True
        result = g.refresh(call, read=lambda cwd: tokens(git_error="!"))
        self.assertEqual(result["child"]["git_summary"], "!")
        self.assertIsNone(result["child"]["git_child_summary"])
        self.assertIsNone(result["ordinary"]["git_summary"])
        self.assertEqual(result["ordinary"]["git_child_summary"], "!")
        paused = g.refresh(call, read=lambda cwd: tokens(git_summary="!"))
        self.assertEqual(paused["ordinary"]["git_child_summary"], "!")
        self.assertIsNone(paused["ordinary"]["git_error"])
        self.assertEqual(paused["child"]["git_summary"], "!")
        cleared = g.refresh(call, read=read, clear=True)
        self.assertTrue(all(state == tokens() for state in cleared.values()))
        clean = g.refresh(call, read=lambda cwd: tokens())
        self.assertTrue(all(state == tokens() for state in clean.values()))

    def test_worktree_path_wins_and_no_agent_required(self):
        snapshot = {"workspaces": [
            {"workspace_id": "w1", "worktree": {"checkout_path": "/linked"}},
            {"workspace_id": "w2"}, {"workspace_id": "w3"}],
            "panes": [{"workspace_id": "w1", "cwd": "/wrong", "foreground_cwd": "/"},
                      {"workspace_id": "w2", "cwd": "/ordinary"},
                      {"workspace_id": "w2", "cwd": "/second"}]}
        self.assertEqual(list(g.spaces(snapshot)),
                         [("w1", "/linked"), ("w2", "/ordinary"), ("w3", None)])

    def test_every_refresh_reports_all_tokens(self):
        calls = []
        def call(method, params=None):
            if method == "session.snapshot":
                return {"snapshot": {"panes": [], "workspaces": [{"workspace_id": "live"}]}}
            calls.append(params)
            return {}
        for state in [tokens(git_changes="*", git_sync="↑1 ↓2", git_ahead="↑1", git_behind="↓2"), tokens(),
                      tokens(git_error="!")]:
            self.assertEqual(g.refresh(call, read=lambda cwd: state), {"live": state})
            self.assertEqual(calls[-1]["tokens"], state)
            self.assertIsNone(calls[-1]["tokens"]["git_dirty"])

    def test_clear_refresh_ttl_and_workspace_race(self):
        calls = []
        def call(method, params=None):
            calls.append((method, params))
            if method == "session.snapshot":
                return {"snapshot": {"panes": [], "workspaces": [
                    {"workspace_id": "closed"}, {"workspace_id": "live"}]}}
            if params["workspace_id"] == "closed":
                raise RuntimeError("workspace_not_found")
            return {}
        self.assertEqual(g.refresh(call, clear=True), {"live": tokens()})
        self.assertEqual(calls[-1][1]["ttl_ms"], 15000)
        self.assertEqual(calls[-1][1]["source"], "git-status")
        self.assertEqual(g.INTERVAL, 5)

    def test_slow_reads_refresh_before_expiry_and_expire_after_failure(self):
        # Four Git reads take virtual 3s each (the old serial loop took 12s),
        # then include a 5s timeout-sized slow repository. No wall-clock sleeps.
        for durations in [(3, 3, 3, 3), (3, 3, 3, 5)]:
            with self.subTest(durations=durations):
                clock = [0]
                failed = [False]
                reports = {}
                active = set()
                lock = threading.Lock()
                barrier = threading.Barrier(4, action=lambda: clock.__setitem__(
                    0, clock[0] + max(durations)))
                snapshot = {"panes": [], "workspaces": [
                    {"workspace_id": str(i), "worktree": {"checkout_path": str(i)}}
                    for i in range(4)]}

                def call(method, params=None):
                    if failed[0]:
                        raise OSError("server unavailable")
                    if method == "session.snapshot":
                        return {"snapshot": snapshot}
                    wid = params["workspace_id"]
                    if wid in reports:
                        self.assertLess(clock[0], reports[wid][1])
                    reports[wid] = (clock[0], clock[0] + params["ttl_ms"] / 1000)
                    self.assertEqual(params["ttl_ms"], 15000)
                    self.assertEqual(params["tokens"], tokens(git_changes="*"))

                def read(cwd):
                    with lock:
                        active.add(threading.get_ident())
                    barrier.wait(timeout=2)
                    return tokens(git_changes="*")

                g.refresh(call, read)
                first = reports["0"][0]
                clock[0] += g.INTERVAL
                g.refresh(call, read)
                self.assertEqual(reports["0"][0] - first, g.INTERVAL + max(durations))
                self.assertLessEqual(len(active), 8)  # Two joined pools of four.
                self.assertEqual(len(reports), 4)
                # A failed reporter does not renew metadata: it really expires.
                failed[0] = True
                previous = dict(reports)
                clock[0] += g.INTERVAL
                with self.assertRaises(OSError):
                    g.refresh(call, read)
                self.assertEqual(reports, previous)
                clock[0] += g.TTL_MS / 1000 - g.INTERVAL
                self.assertTrue(all(clock[0] >= expires for _, expires in reports.values()))

    def test_large_groups_have_bounded_workers_and_adaptive_ttl(self):
        snapshot = {"panes": [], "workspaces": [
            {"workspace_id": str(i)} for i in range(9)]}
        calls = []
        def call(method, params=None):
            if method == "session.snapshot":
                return {"snapshot": snapshot}
            calls.append(params)
        with patch.object(g, "ThreadPoolExecutor", wraps=g.ThreadPoolExecutor) as pool:
            g.refresh(call, read=lambda cwd: tokens())
            pool.assert_called_once_with(max_workers=4)
        self.assertEqual([p["workspace_id"] for p in calls], list(map(str, range(9))))
        ttl = calls[0]["ttl_ms"]
        self.assertGreater(ttl, (g.INTERVAL + 3 * g.GIT_TIMEOUT + 10 * 5) * 1000)
        self.assertTrue(all(p["ttl_ms"] == ttl for p in calls))
        self.assertEqual(g.refresh_ttl(10**9), g.MAX_TTL_MS)

    def test_group_growth_renews_old_workspaces_before_new_batches(self):
        clock = [0]
        reports = {}
        count = [4]
        barrier = threading.Barrier(4, action=lambda: clock.__setitem__(0, clock[0] + 5))
        def read(cwd):
            barrier.wait(timeout=2)
            return tokens(git_changes="*")
        def call(method, params=None):
            if method == "session.snapshot":
                # New workspaces deliberately precede the old ones.
                ids = list(range(4, count[0])) + list(range(4))
                return {"snapshot": {"panes": [], "workspaces": [
                    {"workspace_id": str(i)} for i in ids]}}
            wid = params["workspace_id"]
            if wid in reports:
                self.assertLess(clock[0], reports[wid])
            reports[wid] = clock[0] + params["ttl_ms"] / 1000
        old = g.refresh(call, read)
        clock[0] += g.INTERVAL
        count[0] = 12
        grown = g.refresh(call, read, previous_order=old)
        self.assertEqual(list(grown)[:4], list(old))
        self.assertEqual(clock[0], 25)
        self.assertTrue(all(clock[0] < expiry for expiry in reports.values()))
        clock[0] += g.INTERVAL
        g.refresh(call, read, previous_order=grown)
        self.assertTrue(all(clock[0] < expiry for expiry in reports.values()))

    def test_disable_joins_workers_and_skips_queued_reads(self):
        stop = threading.Event()
        entered = threading.Barrier(4)
        threads = set()
        reads = []
        snapshot = {"panes": [], "workspaces": [
            {"workspace_id": str(i)} for i in range(20)]}
        def call(method, params=None):
            self.assertEqual(method, "session.snapshot")
            return {"snapshot": snapshot}
        def read(cwd):
            threads.add(threading.current_thread())
            reads.append(cwd)
            entered.wait(timeout=2)
            stop.set()
            return tokens()
        self.assertEqual(g.refresh(call, read, should_stop=stop.is_set), {})
        self.assertEqual(len(reads), 4)
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_session_namespaces(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root,
                                         "HERDR_SOCKET_PATH": "/tmp/a.sock"}):
                a = g.session_files()
                self.assertEqual(a, g.session_files())
            with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": root,
                                         "HERDR_SOCKET_PATH": "/tmp/b.sock"}):
                self.assertNotEqual(a, g.session_files())


class DaemonLifecycle(unittest.TestCase):
    def test_duplicate_daemon_exits_without_reporting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / "lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(g, "refresh") as refresh:
                    g.daemon(root)
                    refresh.assert_not_called()

    def test_disable_waits_for_lock_before_enable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / "lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                observations = []

                def old_reporter():
                    deadline = time.monotonic() + 2
                    while not (root / "disabled").exists():
                        if time.monotonic() >= deadline:
                            return
                        time.sleep(0.01)
                    time.sleep(0.1)
                    observations.append("released")
                    fcntl.flock(lock, fcntl.LOCK_UN)

                worker = threading.Thread(target=old_reporter)
                worker.start()
                try:
                    with patch.object(g, "session_files", return_value=root), \
                         patch.object(g, "refresh", side_effect=lambda **kw: observations.append("clear")), \
                         patch.object(g.subprocess, "Popen") as spawn:
                        with patch("sys.argv", ["git_status.py", "disable"]):
                            g.main()
                        self.assertEqual(observations, ["released", "clear"])
                        with (root / "lock").open("a") as probe:
                            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        with patch("sys.argv", ["git_status.py", "enable"]):
                            g.main()
                        spawn.assert_called_once()
                        self.assertFalse((root / "disabled").exists())
                finally:
                    worker.join(timeout=3)
                self.assertFalse(worker.is_alive())

    def test_disable_timeout_is_bounded_and_keeps_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / "lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(g, "session_files", return_value=root), \
                     patch.object(g, "DISABLE_TIMEOUT", 0.05), \
                     patch.object(g, "refresh") as refresh, \
                     patch("sys.argv", ["git_status.py", "disable"]):
                    started = time.monotonic()
                    with self.assertRaises(TimeoutError):
                        g.main()
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertTrue((root / "disabled").exists())
                    refresh.assert_not_called()

    def test_daemon_disable_clears_before_releasing_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = []

            def report(clear=False, should_stop=None, previous_order=()):
                with (root / "lock").open("a") as probe:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                reports.append(clear)
                (root / "disabled").touch()
                return {}

            with patch.object(g, "refresh", side_effect=report):
                g.daemon(root)
            self.assertEqual(reports, [False, True])
            g.wait_for_daemon(root)

    def test_restore_honors_disable_and_enable_removes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "disabled").touch()
            with patch.object(g, "session_files", return_value=root), \
                 patch.object(g.subprocess, "Popen") as spawn:
                with patch("sys.argv", ["git_status.py", "restore"]):
                    g.main()
                spawn.assert_not_called()
                with patch("sys.argv", ["git_status.py", "enable"]):
                    g.main()
                self.assertFalse((root / "disabled").exists())
                spawn.assert_called_once()
                self.assertTrue(spawn.call_args.kwargs["start_new_session"])

    def test_disable_clears_and_marks_only_this_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(g, "session_files", return_value=root), \
                 patch.object(g, "refresh") as refresh, \
                 patch("sys.argv", ["git_status.py", "disable"]):
                g.main()
                refresh.assert_called_once_with(clear=True)
                self.assertTrue((root / "disabled").exists())


class GitFailures(unittest.TestCase):
    def test_operation_read_uses_remaining_timeout_and_failures_warn(self):
        with tempfile.TemporaryDirectory() as temp:
            results = [subprocess.CompletedProcess([], 0, HEADER, b""),
                       subprocess.CompletedProcess([], 0, os.fsencode(temp) + b"\n", b"")]
            with patch.object(g.time, "monotonic", side_effect=[10, 13]), \
                 patch.object(g.subprocess, "run", side_effect=results) as run:
                self.assertEqual(g.git_tokens(temp), tokens())
                self.assertEqual(run.call_args_list[0].kwargs["timeout"], g.GIT_TIMEOUT)
                self.assertEqual(run.call_args_list[1].kwargs["timeout"], 2)
                self.assertEqual(run.call_args_list[1].args[0],
                                 ["git", "--no-optional-locks", "rev-parse", "--absolute-git-dir"])
        for failure in [subprocess.CompletedProcess([], 128, b"", b"unreadable"),
                        subprocess.CompletedProcess([], 0, b"relative-path\n", b""),
                        OSError("unreadable"), subprocess.TimeoutExpired("git", 2)]:
            with self.subTest(failure=failure), patch.object(g.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0, HEADER + ordinary(b"file"), b""), failure]):
                self.assertEqual(g.git_tokens("/repo"), tokens(git_changes="*", git_error="!"))
        with patch.object(g.time, "monotonic", side_effect=[10, 15]), \
             patch.object(g.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, HEADER, b"")) as run:
            self.assertEqual(g.git_tokens("/repo"), tokens(git_error="!"))
            self.assertEqual(run.call_count, 1)

    def test_failure_timeout_missing_git_and_bad_output(self):
        for failure in [OSError("missing git"), subprocess.TimeoutExpired("git", 5)]:
            with self.subTest(failure=failure), patch.object(g.subprocess, "run", side_effect=failure):
                self.assertEqual(g.git_tokens("/repo"), tokens(git_error="!"))
        for code, output, stderr in [(128, b"", b"fatal: index corrupt"),
                                     (0, b"1 malformed\0", b"")]:
            with patch.object(g.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], code, output, stderr)):
                self.assertEqual(g.git_tokens("/repo"), tokens(git_error="!"))

    def test_malformed_output_never_looks_clean(self):
        for output in [b"", b"! ignored\0", b"# branch.head main\0",
                       b"# branch.oid " + b"a" * 40 + b"\0",
                       b"# branch.oid \0# branch.head main\0",
                       b"# branch.oid nope\0# branch.head main\0",
                       b"# branch.oid (initial)\0# branch.head \0",
                       HEADER[:-1], HEADER + ordinary(b"file")[:-1],
                       HEADER + b"2 R. N... 100644 100644 100644 abc abc R100 new\0",
                       HEADER + b"2 R. N... 100644 100644 100644 abc abc R100 new\0\0",
                       HEADER + b"# branch.ab +? -0\0",
                       HEADER + b"# branch.ab +1 +2\0"]:
            with self.subTest(output=output), patch.object(
                    g.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
                self.assertEqual(g.git_tokens("/repo"), tokens(git_error="!"))

    def test_discovery_errors_only_are_quiet(self):
        for stderr, expected in [
                (b"fatal: not a git repository: /repo/.git/worktrees/broken", "!"),
                (b"fatal: not a git repository (or any parent up to mount point /)\n"
                 b"Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).", None),
                (b"fatal: not a git repository unexpected failure", "!")]:
            with self.subTest(stderr=stderr), patch.object(g.subprocess, "run", return_value=
                    subprocess.CompletedProcess([], 128, b"", stderr)):
                self.assertEqual(g.git_tokens("/repo"), tokens(git_error=expected))

    def test_command_and_nonrepo(self):
        with patch.object(g.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 128, b"", b"fatal: not a git repository (or any of the parent directories): .git")) as run:
            self.assertEqual(g.git_tokens("/outside"), tokens())
            self.assertEqual(run.call_args.args[0], ["git", "--no-optional-locks", "status",
                "--porcelain=v2", "-z", "--branch", "--ahead-behind", "--untracked-files=all"])
            self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        with patch.object(g.subprocess, "run") as run:
            self.assertEqual(g.git_tokens(None), tokens())
            run.assert_not_called()


class GitIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "tracked").write_text("base\n")
        self.git("add", "tracked")
        self.git("commit", "-m", "base")

    def git(self, *args):
        return subprocess.run(["git", "--no-optional-locks", *args], cwd=self.root,
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def assert_state(self, **values):
        self.assertEqual(g.git_tokens(self.root), tokens(git_tracking="↕—", **values))

    def test_clean_untracked_staged_modified_and_ignored(self):
        self.assert_state()
        (self.root / "new\nfile").write_text("new")
        self.assert_state(git_changes="*")
        self.git("add", ".")
        self.assert_state(git_changes="*")
        (self.root / "new\nfile").write_text("modified after staging")
        self.assert_state(git_changes="*")
        (self.root / "tracked").write_text("changed\n")
        (self.root / "another").write_text("new")
        self.assert_state(git_changes="*")
        self.git("add", ".")
        self.git("commit", "-m", "new")
        (self.root / ".git" / "info" / "exclude").write_text("ignored\n")
        (self.root / "ignored").write_text("ignore")
        self.assert_state()
        self.assertFalse((self.root / ".git" / "index.lock").exists())

    def test_rename_newline_paths(self):
        self.git("mv", "tracked", "u UU old\nname")
        self.git("commit", "-m", "old name")
        self.git("mv", "u UU old\nname", "new\nname")
        (self.root / "new\nname").write_text("modified too\n")
        self.assert_state(git_changes="*")

    def test_detached_and_unborn(self):
        self.git("checkout", "--detach")
        self.assertEqual(g.git_tokens(self.root), tokens())
        self.git("checkout", "--orphan", "unborn")
        self.assert_state(git_changes="*")

    def test_upstream_ahead_behind_diverged_and_worktree_sync(self):
        self.git("branch", "upstream")
        self.git("branch", "--set-upstream-to=upstream", "main")
        self.git("config", "status.aheadBehind", "false")
        self.assertEqual(g.git_tokens(self.root), tokens())
        (self.root / "tracked").write_text("main\n")
        self.git("commit", "-am", "main")
        self.assertEqual(g.git_tokens(self.root), tokens(git_sync="↑1", git_ahead="↑1"))
        linked = Path(self.temp.name) / "linked"
        self.git("worktree", "add", "-b", "feature", str(linked))
        self.git("-C", str(linked), "branch", "--set-upstream-to=upstream", "feature")
        self.assertEqual(g.git_tokens(linked), tokens(git_sync="↑1", git_ahead="↑1"))
        (linked / "tracked").write_text("work in progress")
        self.assertEqual(g.git_tokens(linked), tokens(git_sync="↑1", git_ahead="↑1", git_changes="*"))
        self.git("checkout", "upstream")
        (self.root / "tracked").write_text("upstream\n")
        self.git("commit", "-am", "upstream")
        self.git("checkout", "main")
        self.assertEqual(g.git_tokens(self.root), tokens(git_sync="↑1 ↓1", git_ahead="↑1", git_behind="↓1"))
        self.git("reset", "--hard", "upstream~1")
        self.assertEqual(g.git_tokens(self.root), tokens(git_sync="↓1", git_behind="↓1"))

    def test_pruned_upstream_keeps_changes_and_labels_missing(self):
        self.git("branch", "upstream")
        self.git("branch", "--set-upstream-to=upstream", "main")
        self.git("branch", "-D", "upstream")
        (self.root / "tracked").write_text("changed\n")
        self.assertEqual(g.git_tokens(self.root),
                         tokens(git_changes="*", git_tracking="↕?"))
        self.git("checkout", "--detach")
        self.assertEqual(g.git_tokens(self.root), tokens(git_changes="*"))

    def test_broken_linked_metadata_is_unavailable(self):
        linked = Path(self.temp.name) / "broken"
        self.git("worktree", "add", "-b", "broken", str(linked))
        admin = self.root / ".git" / "worktrees" / "broken"
        admin.rename(admin.with_name("removed-admin"))
        failure = subprocess.run(["git", "status"], cwd=linked,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env={**os.environ, "LC_ALL": "C"})
        self.assertNotEqual(failure.returncode, 0)
        # Git 2.56 calls this an invalid gitfile; older versions name the
        # missing admin repository directly. Both are real metadata failures.
        self.assertTrue(failure.stderr.startswith((b"fatal: not a git repository:",
            b"fatal: gitfile does not point to a valid repository:")), failure.stderr)
        self.assertEqual(g.git_tokens(linked), tokens(git_error="!"))

    def test_actual_conflict(self):
        self.git("checkout", "-b", "other")
        (self.root / "tracked").write_text("other\n")
        self.git("commit", "-am", "other")
        self.git("checkout", "main")
        (self.root / "tracked").write_text("main\n")
        self.git("commit", "-am", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other")
        self.assert_state(git_changes="*", git_conflict="✖")

    def test_resolved_merge_still_has_final_warning(self):
        self.git("checkout", "-b", "other")
        (self.root / "tracked").write_text("other\n")
        self.git("commit", "-am", "other")
        self.git("checkout", "main")
        (self.root / "tracked").write_text("main\n")
        self.git("commit", "-am", "main")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other")
        (self.root / "tracked").write_text("resolved\n")
        self.git("add", "tracked")
        self.assert_state(git_changes="*", git_summary="* !")
        self.git("commit", "-m", "resolved")
        self.assert_state()

    def test_paused_markers_use_per_worktree_admin_and_subdirectory(self):
        linked = Path(self.temp.name) / "linked"
        self.git("worktree", "add", "-b", "feature", str(linked))
        nested = linked / "subdir"
        nested.mkdir()
        admin = Path(os.fsdecode(self.git("-C", str(linked), "rev-parse",
                                         "--absolute-git-dir").stdout).rstrip("\n"))
        for name in ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                     "rebase-merge", "rebase-apply"]:
            with self.subTest(name=name):
                marker = admin / name
                if name.startswith("rebase-"):
                    marker.mkdir()
                else:
                    marker.write_bytes(self.git("rev-parse", "HEAD").stdout)
                self.assertEqual(g.git_tokens(nested), tokens(git_tracking="↕—", git_summary="!"))
                self.assert_state()  # Main checkout must not inherit child operation state.
                if marker.is_dir():
                    marker.rmdir()
                else:
                    marker.unlink()
        self.assertEqual(g.git_tokens(nested), tokens(git_tracking="↕—"))

    def test_nonrepo_and_missing_directory(self):
        self.assertEqual(g.git_tokens(self.temp.name), tokens())
        self.assertEqual(g.git_tokens(Path(self.temp.name) / "missing"), tokens(git_error="!"))


if __name__ == "__main__":
    unittest.main()
