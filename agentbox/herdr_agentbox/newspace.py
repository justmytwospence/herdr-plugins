"""A space the user opens becomes a new box.

The plugin's `workspace.created` hook types
`eval "$(agentbox-space new-space --workspace <id>)"` into the new space's pane.
That asks (on the terminal) for a repo, an agent and where to run it, queues the
box on the hub, waits for it, and prints the shell code that moves the pane into
the box's directory and starts `agentbox-space box`: herdr restores a pane in its
shell's directory, which is how the daemon finds the pane again after a restart.
Esc at any question leaves a plain shell.

Spaces the daemon opens for existing boxes, herdr worktree spaces, and spaces
with more than one pane are left alone.
"""

from __future__ import annotations

import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import config, herdr, hub, state

GH_CACHE_S = 24 * 3600
CREATE_TIMEOUT_S = 20 * 60


# ---- the hook -----------------------------------------------------------------

def should_handle(workspace: Dict[str, Any], root_cwd: Optional[str], enabled: bool) -> bool:
    if not enabled or workspace.get("worktree"):
        return False
    if int(workspace.get("pane_count") or 1) != 1 or int(workspace.get("tab_count") or 1) != 1:
        return False
    if config.box_of_dir(root_cwd):
        return False  # the daemon's own space for an existing box
    if workspace.get("label") in state.pending_names():
        return False
    return True


def handle(event_json: str, path: Optional[str] = None) -> Optional[str]:
    try:
        event = json.loads(event_json or "{}")
    except ValueError:
        return None
    workspace = ((event.get("data") or {}).get("workspace")) or {}
    workspace_id = workspace.get("workspace_id")
    if not workspace_id:
        return None
    root = next((p for p in herdr.call("pane.list", {}, path).get("panes") or []
                 if p.get("workspace_id") == workspace_id), None)
    if not root:
        return None
    if not should_handle(workspace, root.get("cwd"), bool(config.settings()["new_space_creates_box"])):
        return None
    command = 'eval "$(%s new-space --workspace %s)"' % (shlex.quote(config.command_path()),
                                                       shlex.quote(workspace_id))
    herdr.call("pane.send_text", {"pane_id": root["pane_id"], "text": command}, path)
    herdr.call("pane.send_keys", {"pane_id": root["pane_id"], "keys": ["enter"]}, path)
    return workspace_id


def on_workspace_created() -> int:
    try:
        handle(os.environ.get("HERDR_PLUGIN_EVENT_JSON", ""))
    except (herdr.Unavailable, herdr.HerdrError):
        pass
    return 0


# ---- the picker ---------------------------------------------------------------

def tty() -> Any:
    try:
        return open("/dev/tty", "r+")
    except OSError:
        return sys.stderr


def say(msg: str, end: str = "\n") -> None:
    t = tty()
    t.write(msg + end)
    t.flush()


def normalize_repo(text: str) -> Optional[str]:
    """owner/repo from a URL, an scp-style remote or owner/repo itself."""
    text = (text or "").strip()
    m = re.search(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", text)
    if m:
        return "%s/%s" % (m.group(1), m.group(2))
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", text)
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def gh_repos() -> List[str]:
    """`gh repo list` for the signed-in user, cached for a day; [] without gh."""
    cache = config.state_path("gh-repos.json")
    try:
        if time.time() - os.path.getmtime(cache) < GH_CACHE_S:
            with open(cache) as f:
                return list(json.load(f))
    except (OSError, ValueError):
        pass
    if not shutil.which("gh"):
        return []
    try:
        out = subprocess.run(["gh", "repo", "list", "--limit", "200", "--json", "nameWithOwner",
                              "--jq", ".[].nameWithOwner"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    repos = [r for r in out.stdout.split() if "/" in r] if out.returncode == 0 else []
    if repos:
        with open(cache, "w") as f:
            json.dump(repos, f)
    return repos


def known_repos(boxes: List[Dict[str, Any]]) -> List[str]:
    """Repos of the hub's boxes first (most recent first), then gh's list."""
    seen: List[str] = []
    for b in sorted(boxes, key=lambda b: -float(b.get("createdAt") or 0)):
        r = normalize_repo(str(b.get("originUrl") or "")) or normalize_repo(str(b.get("repo") or ""))
        if r and r not in seen:
            seen.append(r)
    for r in gh_repos():
        if r not in seen:
            seen.append(r)
    return seen


def choose(prompt: str, options: List[str], allow_typed: bool = False) -> Optional[str]:
    """fzf when installed, a numbered menu otherwise. None when cancelled."""
    if shutil.which("fzf"):
        header = "Enter picks" + ("; type owner/repo for one not listed" if allow_typed else "") + "; Esc: plain shell"
        proc = subprocess.run(
            ["fzf", "--prompt", "%s> " % prompt, "--print-query", "--layout=reverse", "--height=60%",
             "--header", header, "--no-sort"],
            input="\n".join(options), stdout=subprocess.PIPE, text=True)
        if proc.returncode not in (0, 1):
            return None
        query, selected = (proc.stdout.split("\n") + ["", ""])[:2]
        return selected or (query if allow_typed and query else None)
    t = tty()
    t.write("\n%s:\n" % prompt)
    for n, opt in enumerate(options, 1):
        t.write("  %2d) %s\n" % (n, opt))
    t.write("number%s (empty: plain shell): " % (" or owner/repo" if allow_typed else ""))
    t.flush()
    answer = t.readline().strip() if t is not sys.stderr else input().strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return answer if allow_typed and answer else None


def box_name(repo: str, taken: List[str], rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    short = re.sub(r"[^a-z0-9-]+", "-", repo.split("/")[-1].lower()).strip("-")[:24] or "box"
    for _ in range(100):
        name = "%s-%04x" % (short, rng.randrange(0x10000))
        if name not in taken:
            return name
    return "%s-%d" % (short, int(time.time()))


def wait_for(name: str, job_id: str) -> Optional[str]:
    """The new box's id once the hub lists it, or None (failed, timed out)."""
    start = time.time()
    while time.time() - start < CREATE_TIMEOUT_S:
        try:
            boxes = hub.boxes()
        except hub.HubError as e:
            say("\r  hub: %s" % e, end="")
            boxes = []
        for b in boxes:
            if b.get("name") != name:
                continue
            if not str(b.get("id", "")).startswith("job:"):
                return str(b["id"])
            if b.get("status") == "error":
                say("\n  create failed: %s" % (b.get("error") or "see the hub's web UI"))
                return None
        say("\r  creating %s ... %ds " % (name, time.time() - start), end="")
        time.sleep(4)
    say("\n  still not created after %d min; it may yet appear (it gets its own space then)"
        % (CREATE_TIMEOUT_S // 60))
    return None


def pick(workspace_id: str) -> str:
    """Shell code for the pane: into the new box, or nothing (stay a plain shell)."""
    cfg = config.settings()
    boxes = hub.boxes()
    repo = choose("repo for a new box", known_repos(boxes), allow_typed=True)
    repo = normalize_repo(repo or "")
    if not repo:
        return ""
    agent = choose("agent", list(cfg["agents"]))
    if not agent:
        return ""
    labels = [str(p[0]) for p in cfg["providers"]]
    where = choose("where", labels)
    if not where:
        return ""
    provider = dict((str(p[0]), str(p[1])) for p in cfg["providers"])[where]
    say("first prompt (empty: start interactive): ", end="")
    t = tty()
    prompt = (t.readline() if t is not sys.stderr else input()).strip()

    name = box_name(repo, [str(b.get("name")) for b in boxes])
    state.add_pending(name, workspace_id)
    herdr.quiet("workspace.rename", {"workspace_id": workspace_id, "label": name})
    say("%s: %s with %s on %s" % (name, repo, agent, where))
    try:
        job = hub.create(repo, agent, provider, name, prompt or None)
        box_id = wait_for(name, job)
    finally:
        state.drop_pending(name)
    if not box_id:
        return ""
    # The hub lists a new box's agent as claude until it reports; the pane reads this.
    with open(os.path.join(config.box_dir(box_id), ".agent"), "w") as f:
        f.write(agent)
    say("")
    return "cd %s && %s box %s" % (shlex.quote(config.box_dir(box_id)), shlex.quote(config.command_path()),
                                   shlex.quote(box_id))


def main(workspace_id: Optional[str]) -> int:
    workspace_id = workspace_id or os.environ.get("HERDR_WORKSPACE_ID") or ""
    try:
        print(pick(workspace_id))
    except hub.HubError as e:
        say("agentbox-space: %s" % e)
        print("")
    except KeyboardInterrupt:
        print("")
    return 0
