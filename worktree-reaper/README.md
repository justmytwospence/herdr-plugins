# worktree-reaper

A Python-standard-library Herdr plugin that removes git worktrees once their work has landed,
together with their Herdr space and local branch. It works for any agent (Pi, Claude Code,
Codex) or none: it reads only git and Herdr.

## What it removes

A linked worktree under `<repo>/.worktrees/` (never pi-subagents' `subagent-*`, a locked,
prunable or detached one, or the main checkout) is removed when **all** of these hold:

- **Landed.** Its branch tip is contained in `origin/HEAD` (origin's default branch, such as
  `dev` or `main`; else `origin/main`) after a `git fetch --prune`, which covers local merges,
  fast-forwards, rebase landings and merge-commit pull requests. Failing that, a merged pull
  request whose head is the tip (squash merges) counts, checked with a signed-in `gh`
  (cached for 30 minutes; skipped without `gh`).
- **Has work.** The branch's reflog holds more than its creation entry. A fresh worktree sits at
  the tip of the default branch and would otherwise look landed.
- **Clean.** `git status --porcelain --untracked-files=all` is empty (ignored files such as
  `node_modules` are fine).
- **Idle.** Its space, if open, is not focused and has no `working` or `blocked` agent.
- **Grace.** It stayed landed for 2 minutes, or for 4 hours when an agent (even an idle one) is
  still in its space, since a session often merges and then waits for feedback. A new commit
  resets the timer.

Removal uses Herdr's `worktree.remove` for an open space (closing the space and removing the
checkout), else `git worktree remove`, then `git branch -D` (squash merges are not ancestors).
It never forces and never trusts a repository for you; a refusal is logged and skipped. One
notification per removal.

Repositories are discovered from the spaces open in Herdr; a repository with no open space is
not scanned. The pass runs every 60 seconds and fetches each repository at most every 5
minutes.

## Space token

`landed` is set on a pending worktree's space, for example `✓ dev · 3h` (`PR` when a merged pull
request was the evidence) counting down to its removal. Add it to a sidebar row in Herdr's
config:

```toml
{ token = "$landed", dim = true },
```

## Actions

| Action | What it does |
| --- | --- |
| `worktree-reaper.enable` | Start the background pass (a startup hook restores it) |
| `worktree-reaper.disable` | Stop it and clear the tokens |
| `worktree-reaper.remove-landed` | Run one pass now, ignoring the grace period (still never a busy or dirty one), and print what it removed |

## Configuration

Optional `$HERDR_PLUGIN_CONFIG_DIR/config.json` (`~/.config/herdr/plugins/config/worktree-reaper/`):

```json
{ "grace_empty_s": 120, "grace_agent_s": 14400 }
```

State and the daemon log are in `~/.local/state/herdr/plugins/worktree-reaper/<socket hash>/`
(`daemon.log`, `state.json`).

## Install

```sh
herdr plugin install justmytwospence/herdr-plugins/worktree-reaper --ref <commit> --yes
herdr plugin action invoke worktree-reaper.enable
```

Requires Python 3.9+, Git and Herdr 0.9.5+ on macOS or Linux; `gh` is optional.

## Tests

```sh
cd worktree-reaper && python3 -B -m unittest discover -s tests
```
