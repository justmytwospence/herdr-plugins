#!/usr/bin/env python3
"""Read-only, session-scoped working-tree metadata for Herdr spaces."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

INTERVAL = 5
TTL_MS = 15000
MAX_WORKERS = 4
GIT_TIMEOUT = 5
MAX_TTL_MS = 24 * 60 * 60 * 1000
DISABLE_TIMEOUT = 30


def rpc(method, params=None):
    return herdr_rpc.call(method, params, timeout=5, max_bytes=16 * 1024 * 1024, request_id="git-status")


def empty_tokens():
    # Include the retired token so reports also migrate existing source state.
    return dict.fromkeys(("git_conflict", "git_changes", "git_sync",
                          "git_ahead", "git_behind", "git_tracking", "git_error", "git_dirty",
                          "git_summary", "git_child_summary"))


def summary(tokens, warning=False):
    """One row item: native separators must never split the Git marks."""
    return " ".join(part for part in (
        tokens.get("git_changes"), tokens.get("git_ahead"), tokens.get("git_behind"),
        "!" if warning or tokens.get("git_conflict") or tokens.get("git_error") else None
    ) if part) or None


def parse_status(data):
    """Parse porcelain v2 NUL records without interpreting path contents."""
    tokens = empty_tokens()
    if not data or not data.endswith(b"\0"):
        raise ValueError("Missing or truncated porcelain output")
    records = iter(data[:-1].split(b"\0"))
    changed, conflicts = set(), set()
    branch = {}
    for record in records:
        if not record:
            raise ValueError("Empty porcelain record")
        if record.startswith(b"# "):
            key, value = record[2:].split(b" ", 1)
            if not value or key in branch:
                raise ValueError("Malformed branch metadata")
            branch[key] = value
        elif record.startswith(b"1 "):
            changed.add(record.split(b" ", 8)[8])
        elif record.startswith(b"2 "):
            changed.add(record.split(b" ", 9)[9])
            # Original path is a separate NUL record, never a status record.
            if not next(records, None):
                raise ValueError("Missing rename origin")
        elif record.startswith(b"u "):
            path = record.split(b" ", 10)[10]
            changed.add(path)
            conflicts.add(path)
        elif record.startswith(b"? "):
            changed.add(record[2:])
        elif not record.startswith(b"! "):
            raise ValueError("Unknown porcelain record")
    head, oid = branch.get(b"branch.head"), branch.get(b"branch.oid")
    if not head or not oid:
        raise ValueError("Missing branch head or oid")
    if oid != b"(initial)" and (len(oid) not in (40, 64) or
                               any(c not in b"0123456789abcdef" for c in oid)):
        raise ValueError("Malformed branch oid")
    if changed:
        tokens["git_changes"] = "*"
    if conflicts:
        tokens["git_conflict"] = "✖"
    if head != b"(detached)":
        if b"branch.upstream" not in branch:
            tokens["git_tracking"] = "↕—"
        elif oid != b"(initial)" and b"branch.ab" not in branch:
            tokens["git_tracking"] = "↕?"
    if b"branch.ab" in branch:
        ahead, behind = branch[b"branch.ab"].split()
        if (not ahead.startswith(b"+") or not ahead[1:].isdigit() or
                not behind.startswith(b"-") or not behind[1:].isdigit()):
            raise ValueError("Malformed ahead/behind counts")
        ahead, behind = int(ahead), -int(behind)
        tokens["git_ahead"] = f"↑{ahead}" if ahead else None
        tokens["git_behind"] = f"↓{behind}" if behind else None
        tokens["git_sync"] = " ".join(
            part for part in (tokens["git_ahead"], tokens["git_behind"]) if part) or None
    tokens["git_summary"] = summary(tokens)
    return tokens


def git_tokens(cwd):
    tokens = empty_tokens()
    if not cwd:
        return tokens
    try:
        deadline = time.monotonic() + GIT_TIMEOUT
        result = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain=v2", "-z",
             "--branch", "--ahead-behind", "--untracked-files=all"], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT,
            check=False, env={**os.environ, "LC_ALL": "C"})
        if result.returncode == 0:
            tokens = parse_status(result.stdout)
            # Resolve the per-worktree admin directory with Git, not by guessing
            # .git paths (linked checkouts and pane subdirectories differ).
            # Both commands share one timeout budget, preserving the TTL bound.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("git", GIT_TIMEOUT)
            admin = subprocess.run(
                ["git", "--no-optional-locks", "rev-parse", "--absolute-git-dir"],
                cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=remaining, check=False, env={**os.environ, "LC_ALL": "C"})
            if admin.returncode != 0:
                raise ValueError("Cannot read Git operation state")
            # Remove only Git's terminator, preserving newlines in directory names.
            gitdir = Path(os.fsdecode(admin.stdout.removesuffix(b"\n")))
            if not gitdir.is_absolute() or not gitdir.is_dir():
                raise ValueError("Invalid Git admin directory")
            paused = False
            for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                         "rebase-merge", "rebase-apply"):
                try:
                    (gitdir / name).stat()
                except FileNotFoundError:
                    continue
                paused = True
                break
            tokens["git_summary"] = summary(tokens, warning=paused)
            return tokens
        # Only an explicit non-repository result is blank; all other failures
        # (including inaccessible/missing checkouts) must remain visible.
        if result.stderr.startswith((
                b"fatal: not a git repository (or any of the parent directories):",
                b"fatal: not a git repository (or any parent up to mount point ")):
            return tokens
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    tokens["git_error"] = "!"
    tokens["git_summary"] = summary(tokens)
    return tokens


def spaces(snapshot):
    first_cwds = {}
    for pane in snapshot["panes"]:
        if pane.get("cwd"):
            first_cwds.setdefault(pane["workspace_id"], pane["cwd"])
    for workspace in snapshot["workspaces"]:
        wid = workspace["workspace_id"]
        checkout = (workspace.get("worktree") or {}).get("checkout_path")
        yield wid, checkout or first_cwds.get(wid)


def refresh_ttl(workspace_count):
    # Batch collection takes at most ceil(N/workers) native Git timeouts.
    # Large groups also budget the bounded snapshot/report RPCs (5s each).
    # Keep the normal small-group TTL; extra slack covers scheduling/reporting.
    batches = (workspace_count + MAX_WORKERS - 1) // MAX_WORKERS
    budget = INTERVAL + batches * GIT_TIMEOUT + 5
    if workspace_count > MAX_WORKERS:
        budget += (workspace_count + 1) * 5
    return min(MAX_TTL_MS, max(TTL_MS, budget * 1000))


def refresh(call=rpc, read=git_tokens, clear=False, should_stop=None, previous_order=()):
    snapshot = call("session.snapshot")["snapshot"]
    workspaces = list(spaces(snapshot))
    linked_children = {workspace["workspace_id"] for workspace in snapshot["workspaces"]
                       if (workspace.get("worktree") or {}).get("is_linked_worktree")}
    # Renew existing workspaces first when a group grows: their previous TTL
    # must not lapse while newly added checkouts consume additional batches.
    priority = {wid: i for i, wid in enumerate(previous_order)}
    workspaces.sort(key=lambda item: priority.get(item[0], len(priority)))
    stopped = should_stop or (lambda: False)
    ttl = TTL_MS if clear else refresh_ttl(len(workspaces))

    def collect(cwd):
        # Queued work becomes a no-op on disable; only up to MAX_WORKERS
        # running Git commands need to finish their native timeout.
        return empty_tokens() if stopped() else {**empty_tokens(), **read(cwd)}

    results = {}

    def publish(batch, states):
        for (wid, _), tokens in zip(batch, states):
            if not clear and stopped():
                break
            # Classify and route on the publishing thread from this snapshot.
            # Linked children already use their branch as the row-one label;
            # a row-two summary would increase their existing one-row height.
            value = None if clear else (tokens.get("git_summary") or summary(tokens))
            tokens["git_summary"] = None if wid in linked_children else value
            tokens["git_child_summary"] = value if wid in linked_children else None
            try:
                call("workspace.report_metadata", {"workspace_id": wid,
                     "source": "git-status", "tokens": tokens, "ttl_ms": ttl})
                results[wid] = tokens
            except (OSError, RuntimeError, ValueError) as error:
                # A closed workspace must not prevent the others from refreshing.
                print(f"{wid}: {error}", file=sys.stderr)

    if clear:
        publish(workspaces, [empty_tokens() for _ in workspaces])
    else:
        # Context-managed workers join before clearing/releasing the lock.
        # Publish each bounded batch on the main thread, with no unbounded queue.
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            for offset in range(0, len(workspaces), MAX_WORKERS):
                if stopped():
                    break
                batch = workspaces[offset:offset + MAX_WORKERS]
                states = list(pool.map(collect, (cwd for _, cwd in batch)))
                publish(batch, states)
    return results


def session_files():
    endpoint = os.path.abspath(os.environ["HERDR_SOCKET_PATH"])
    key = hashlib.sha256(endpoint.encode()).hexdigest()[:20]
    root = Path(os.environ["HERDR_PLUGIN_STATE_DIR"]) / key
    root.mkdir(parents=True, exist_ok=True)
    return root


def daemon(root):
    # Kernel-held lock: no stale PID races, no cross-session process killing.
    with (root / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_success = time.monotonic()
        previous_order = ()
        try:
            while not stopping and not (root / "disabled").exists():
                try:
                    reports = refresh(
                        should_stop=lambda: stopping or (root / "disabled").exists(),
                        previous_order=previous_order)
                    previous_order = tuple(reports)
                    last_success = time.monotonic()
                except (OSError, RuntimeError, ValueError) as error:
                    print(error, file=sys.stderr, flush=True)
                    if time.monotonic() - last_success >= 30:
                        break
                for _ in range(INTERVAL * 10):
                    if stopping or (root / "disabled").exists():
                        break
                    time.sleep(0.1)
        finally:
            try:
                refresh(clear=True)
            except (OSError, RuntimeError, ValueError):
                pass  # TTL removes stale tokens if the server has gone away.


def wait_for_daemon(root):
    """Wait for the old reporter's kernel lock, never signal a process."""
    deadline = time.monotonic() + DISABLE_TIMEOUT
    with (root / "lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Git status reporter did not stop before deadline")
                time.sleep(min(0.1, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["enable", "restore", "disable", "once", "daemon"])
    args = parser.parse_args()
    if args.mode == "once":
        print(json.dumps(refresh(), ensure_ascii=False))
        return
    root = session_files()
    disabled = root / "disabled"
    if args.mode == "disable":
        disabled.touch()
        wait_for_daemon(root)
        refresh(clear=True)
    elif args.mode == "daemon":
        daemon(root)
    else:
        if args.mode == "enable":
            disabled.unlink(missing_ok=True)
        elif disabled.exists():
            return
        with (root / "daemon.log").open("ab") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)


if __name__ == "__main__":
    main()
