#!/usr/bin/env python3
"""Remove git worktrees whose branch has landed, with their Herdr space and branch.

Landed: the branch tip is contained in origin's default branch (after a fetch), or a merged
pull request's head is the tip (squash merges, through `gh`). A worktree is removed only when it
is clean, its space is not focused and has no working or blocked agent, and it stayed landed
through a grace period (long when an agent is still in its space).
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import herdr_rpc  # noqa: E402

INTERVAL = 60
FETCH_EVERY = 300
GRACE_EMPTY = 120
GRACE_AGENT = 4 * 3600
GH_CACHE = 1800
GIT_TIMEOUT = 60
TOKEN = "landed"
SOURCE = "worktree-reaper"
TTL_MS = 3 * INTERVAL * 1000
LOCK_TIMEOUT = 30


def rpc(method, params=None):
    return herdr_rpc.call(method, params, timeout=15, max_bytes=16 * 1024 * 1024, request_id=SOURCE)


def git(args, cwd, timeout=GIT_TIMEOUT):
    """Run git without ever raising: a failure to start or a timeout is a returncode-1 result."""
    env = dict(os.environ, LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, env=env, text=True,
                              capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return subprocess.CompletedProcess(args, 1, "", str(error))


def gh_merged_heads(root, branch):
    """Head commits of merged pull requests for `branch`, or None when gh cannot tell."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "merged", "--json", "headRefOid",
             "--limit", "20"], cwd=root, text=True, capture_output=True, timeout=GIT_TIMEOUT)
        if result.returncode != 0:
            return None
        return [pr.get("headRefOid") for pr in json.loads(result.stdout)]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def real(path):
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def short(ref):
    return ref[len("origin/"):] if ref.startswith("origin/") else ref


def default_base(root, run=git):
    """origin/HEAD (for example origin/dev), else origin/main, else None."""
    head = run(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()
    main = run(["rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"], root)
    return "origin/main" if main.returncode == 0 else None


def parse_worktrees(text):
    """`git worktree list --porcelain` as dicts: path, head, branch, detached, locked, prunable."""
    entries, current = [], None
    for line in text.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):], "head": None, "branch": None,
                       "detached": False, "locked": False, "prunable": False, "bare": False}
            entries.append(current)
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            current["prunable"] = True
    return entries


def managed(root, worktree):
    """A checkout this plugin may remove: under <root>/.worktrees, not pi-subagents' own."""
    path = real(worktree["path"])
    prefix = os.path.join(real(root), ".worktrees") + os.sep
    return (path.startswith(prefix) and not os.path.basename(path).startswith("subagent-")
            and bool(worktree.get("branch")) and not worktree.get("detached")
            and not worktree.get("locked") and not worktree.get("prunable")
            and not worktree.get("bare"))


def has_work(root, branch, run=git):
    """The branch got a commit or update after its creation. A fresh worktree sits at the tip of
    origin's default branch and so looks landed; its reflog holds only the creation entry. With
    no reflog the answer is no: never remove what cannot be told from a fresh checkout."""
    log = run(["reflog", "show", "--format=%gs", f"refs/heads/{branch}"], root)
    return log.returncode == 0 and len([line for line in log.stdout.splitlines() if line.strip()]) > 1


def landed(root, branch, sha, base, gh_cache, now, run=git, gh=gh_merged_heads):
    """What the branch landed in ("origin/dev", "a merged pull request"), or None."""
    if run(["merge-base", "--is-ancestor", sha, base], root).returncode == 0:
        return base
    if shutil.which("gh") is None and gh is gh_merged_heads:
        return None
    key = f"{root}\0{branch}\0{sha}"
    cached = gh_cache.get(key)
    if cached is None or now - cached[0] >= GH_CACHE:
        heads = gh(root, branch)
        cached = (now, heads is not None and sha in heads)
        gh_cache[key] = cached
    return "a merged pull request" if cached[1] else None


def clean(path, run=git):
    status = run(["status", "--porcelain", "--untracked-files=all"], path)
    return status.returncode == 0 and not status.stdout.strip()


def remaining(seconds):
    if seconds <= 0:
        return "now"
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))}m"
    return f"{round(seconds / 3600)}h"


def token_text(label, seconds):
    return f"\u2713 {label} \u00b7 {remaining(seconds)}"


def load_config():
    folder = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not folder:
        return {}
    try:
        with open(os.path.join(folder, "config.json")) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remove(root, worktree, space, base, call, run):
    """Remove one landed worktree: its Herdr space (which removes the checkout), else git; then the
    branch. Never forces. Returns the report line, or None when it could not be removed."""
    path, branch = worktree["path"], worktree["branch"]
    name = os.path.basename(real(root))
    if space:
        try:
            call("worktree.remove", {"workspace_id": space})
        except (herdr_rpc.HerdrError, OSError) as error:
            print(f"{name}/{branch}: herdr could not remove it: {error}", file=sys.stderr, flush=True)
            return None
    if os.path.exists(path):
        removed = run(["worktree", "remove", path], root)
        if removed.returncode != 0:
            print(f"{name}/{branch}: git could not remove {path}: {removed.stderr.strip()}",
                  file=sys.stderr, flush=True)
            return None
    deleted = run(["branch", "-D", branch], root)
    if deleted.returncode != 0:
        print(f"{name}/{branch}: branch kept: {deleted.stderr.strip()}", file=sys.stderr, flush=True)
    try:
        call("notification.show", {"title": "Worktree removed",
                                   "body": f"{name}/{branch}: landed in {short(base)}"})
    except (herdr_rpc.HerdrError, OSError):
        pass
    return f"Removed {name}/{branch} ({path}): landed in {base}"


def report(call, space, text):
    try:
        call("workspace.report_metadata", {"workspace_id": space, "source": SOURCE,
                                           "tokens": {TOKEN: text}, "ttl_ms": TTL_MS})
    except (herdr_rpc.HerdrError, OSError) as error:
        print(error, file=sys.stderr, flush=True)


def cycle(state, *, call=rpc, run=git, now=time.time, force=False, config=None, gh=gh_merged_heads):
    """One pass over every repository that has an open space. Returns the removal lines.

    `state` (JSON-serialisable, kept by the caller) holds `fetched` ({root: time}) and `pending`
    ({path: {sha, since, base, space}}); `gh_cache` is in-memory only.
    """
    config = config or {}
    grace_empty = int(config.get("grace_empty_s", GRACE_EMPTY))
    grace_agent = int(config.get("grace_agent_s", GRACE_AGENT))
    moment = now()
    fetched = state.setdefault("fetched", {})
    pending = state.setdefault("pending", {})
    gh_cache = state.setdefault("gh_cache", {})  # in memory only; the daemon keeps one
    snapshot = call("session.snapshot")["snapshot"]
    agents = call("agent.list").get("agents", [])
    busy = {a.get("workspace_id") for a in agents if a.get("agent_status") in ("working", "blocked")}
    occupied = {a.get("workspace_id") for a in agents}
    spaces, roots = {}, set()
    for workspace in snapshot.get("workspaces", []):
        tree = workspace.get("worktree") or {}
        if tree.get("checkout_path"):
            spaces[real(tree["checkout_path"])] = (workspace["workspace_id"], bool(workspace.get("focused")))
        if tree.get("repo_root"):
            roots.add(real(tree["repo_root"]))

    lines, still = [], set()
    for root in sorted(roots):
        if force or moment - fetched.get(root, 0) >= FETCH_EVERY:
            fetch = run(["fetch", "--prune", "--quiet", "origin"], root)
            if fetch.returncode != 0:
                print(f"{root}: fetch failed: {fetch.stderr.strip()}", file=sys.stderr, flush=True)
            else:
                fetched[root] = moment
        base = default_base(root, run)
        if base is None:
            continue
        listing = run(["worktree", "list", "--porcelain"], root)
        if listing.returncode != 0:
            continue
        for worktree in parse_worktrees(listing.stdout):
            if not managed(root, worktree):
                continue
            path, branch = worktree["path"], worktree["branch"]
            sha = run(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root).stdout.strip()
            if not sha or not has_work(root, branch, run):
                continue
            where = landed(root, branch, sha, base, gh_cache, moment, run, gh)
            if where is None:
                continue
            space, focused = spaces.get(real(path), (None, False))
            entry = pending.get(path)
            if not entry or entry.get("sha") != sha:
                entry = pending[path] = {"sha": sha, "since": moment}
            entry.update(base=base, space=space)
            still.add(path)
            grace = grace_agent if space in occupied else grace_empty
            left = grace - (moment - entry["since"])
            if space:
                report(call, space, token_text(short(base) if where == base else "PR", left))
            if not force and left > 0:
                continue
            if space and (space in busy or (focused and not force)):
                continue
            if not clean(path, run):
                continue
            line = remove(root, worktree, space, base, call, run)
            if line:
                lines.append(line)
                pending.pop(path, None)
                still.discard(path)

    for path in [p for p in pending if p not in still]:
        entry = pending.pop(path)
        if entry.get("space"):
            try:
                call("workspace.report_metadata", {"workspace_id": entry["space"], "source": SOURCE,
                                                   "tokens": {TOKEN: None}, "ttl_ms": TTL_MS})
            except (herdr_rpc.HerdrError, OSError):
                pass  # the space is gone, or the TTL clears the token
    return lines


def session_files():
    endpoint = os.path.abspath(os.environ["HERDR_SOCKET_PATH"])
    key = hashlib.sha256(endpoint.encode()).hexdigest()[:20]
    root = Path(os.environ["HERDR_PLUGIN_STATE_DIR"]) / key
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_state(root):
    try:
        data = json.loads((root / "state.json").read_text())
    except (OSError, ValueError):
        data = {}
    return {"fetched": data.get("fetched", {}), "pending": data.get("pending", {})}


def save_state(root, state):
    stored = {"fetched": state.get("fetched", {}), "pending": state.get("pending", {})}
    temp = root / "state.json.tmp"
    temp.write_text(json.dumps(stored))
    os.replace(temp, root / "state.json")


_GH_CACHE = {}


def locked_cycle(root, *, force=False, wait=0, **kwargs):
    """Run one cycle under the cycle lock, so the daemon and `now` never overlap. None when the
    lock stayed busy."""
    deadline = time.monotonic() + wait
    with (root / "cycle.lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.1)
        state = load_state(root)
        state["gh_cache"] = _GH_CACHE
        try:
            return cycle(state, force=force, config=load_config(), **kwargs)
        finally:
            save_state(root, state)


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
        while not stopping and not (root / "disabled").exists():
            try:
                for line in locked_cycle(root) or ():
                    print(line, flush=True)
                last_success = time.monotonic()
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                print(error, file=sys.stderr, flush=True)
                if time.monotonic() - last_success >= 30:
                    break
            for _ in range(INTERVAL * 10):
                if stopping or (root / "disabled").exists():
                    break
                time.sleep(0.1)


def wait_for_daemon(root):
    """Wait for the daemon's kernel lock, never signal a process."""
    deadline = time.monotonic() + LOCK_TIMEOUT
    with (root / "lock").open("a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0:
                    raise TimeoutError("Worktree reaper did not stop before deadline")
                time.sleep(min(0.1, remaining_time))


def clear_tokens(root):
    state = load_state(root)
    for entry in state.get("pending", {}).values():
        if entry.get("space"):
            try:
                rpc("workspace.report_metadata", {"workspace_id": entry["space"], "source": SOURCE,
                                                  "tokens": {TOKEN: None}, "ttl_ms": TTL_MS})
            except (herdr_rpc.HerdrError, OSError):
                pass
    state["pending"] = {}
    save_state(root, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["enable", "restore", "disable", "now", "daemon"])
    args = parser.parse_args()
    root = session_files()
    disabled = root / "disabled"
    if args.mode == "now":
        lines = locked_cycle(root, force=True, wait=LOCK_TIMEOUT)
        if lines is None:
            sys.exit("Another pass is still running; try again in a moment.")
        print("\n".join(lines) if lines else "Nothing to remove.")
    elif args.mode == "disable":
        disabled.touch()
        wait_for_daemon(root)
        clear_tokens(root)
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
