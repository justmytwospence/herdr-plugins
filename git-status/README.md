# herdr-git-status

A Python-standard-library Herdr plugin showing Git state in **every space**,
independent of whether any agent is running. It reports grouped worktree children
without creating pseudo-agents. One summary token holds space-separated marks:
`* ↑2 ↓1 !` (only applicable marks appear). Colors belong in the consuming UI
configuration, not the plugin.

| Token | Visible value |
| --- | --- |
| `git_summary` | `*` for any uncommitted work, then `↑N` / `↓N` for nonzero commit counts, then `!` for conflicts, read failures, or paused operations; ordinary spaces only |
| `git_child_summary` | The same summary, only for `worktree.is_linked_worktree` spaces; `git_summary` is cleared there |
| `git_conflict` | `✖` if any conflicted path exists |
| `git_changes` | `*` if any uncommitted change exists, including staged, unstaged, untracked, and conflicted paths |
| `git_ahead` | `↑N` for a nonzero ahead count; independently colorable |
| `git_behind` | `↓N` for a nonzero behind count; independently colorable |
| `git_sync` | `↑N`, `↓N`, or `↑N ↓N` for nonzero ahead/behind counts; combined token retained for compatibility |
| `git_tracking` | `↕—` for an attached branch without configured tracking (including unborn); `↕?` for a non-unborn attached branch whose configured upstream ref is unavailable |
| `git_error` | `!` when Git cannot determine status |
| `git_dirty` | Always cleared (`null`), migrating the old dot token |

The older individual tokens above remain available for compatibility, but the
recommended layout references only summary fields. Summaries contain no words,
tracking labels, file counts, clean markers, or dots. Absent values are `null` and
hidden; a clean checkout may still show nonzero commit counts. Conflicts and
paused merge, rebase, cherry-pick, or revert operations use one final `!`.
Outside a repository, all tokens are blank; a missing or inaccessible checkout,
failed Git command, timeout, or malformed porcelain output (missing `branch.head`
or `branch.oid`, or truncated NUL records) instead shows `!`, never a falsely
clean result. Every refresh reports all ten tokens, actively clearing obsolete values.

Requires Python 3.9+, Git, and Herdr 0.7.5+ on macOS or Linux. No dependencies or
build step. This plugin is separate from ezcorp-org/herdr-git-status.

## Install locally

```sh
herdr plugin link ~/Projects/herdr-git-status
herdr plugin action invoke git-status.enable
```

Preserve the existing height: ordinary spaces show the name on row one, with
branch, one Git summary, and the existing PR token on row two. The summary is a
single row item, so Herdr cannot insert native separators between its marks.
Linked worktree children are the exception: native branch is suppressed and their
row-one label already names the branch. Only they receive a row-one summary suffix,
keeping their existing one-row height. The other summary field is always cleared.
Fit or truncate within the existing width; never add rows:

```toml
[ui.sidebar.spaces]
rows = [
  ["state_icon", "workspace",
   { token = "$git_child_summary", fg = "#87af87", rules = [
     { contains = "!", fg = "#d75f5f", bold = true },
     { contains = "↓", fg = "#d75f5f" },
     { contains = "*", fg = "#d7af00" }] }],
  ["branch",
   { token = "$git_summary", fg = "#87af87", rules = [
     { contains = "!", fg = "#d75f5f", bold = true },
     { contains = "↓", fg = "#d75f5f" },
     { contains = "*", fg = "#d7af00" }] },
   { token = "$pr", fg = "#5f87d7" }],
]
```

Herdr styles the whole summary token: red for `!` or `↓`, otherwise yellow
for `*`, otherwise green for `↑`. The first matching rule wins. Other interfaces
can color individual marks; this grouped severity style avoids dot separators
without requiring a custom Herdr renderer.

Do not add individual Git mark tokens to these rows: separate row items allow
native separators between marks and would duplicate the summary.

Then `herdr server reload-config`. For an existing server, linking does not run
the startup hook: invoke `git-status.enable` explicitly. New servers start the
reporter automatically. `git-status.disable` stops and clears all ten of this session's
tokens and keeps it disabled across restarts; enable reverses that. Disable waits
for a bounded interval for the reporter's kernel-held lock to be released. If the
lock is not released in time, it returns a timeout error rather than killing the
reporter. The `git-status.refresh` action does a one-shot refresh without starting
a daemon.
Actions use the session targeted by the Herdr CLI; remote hosts need their own
installation and config.

## Behavior

The reporter reads one atomic `session.snapshot` per refresh, then waits five
seconds after the refresh completes. Independent Git reads run concurrently with
at most four workers and a shared five-second Git timeout per checkout. Each batch is
published on the main thread before collecting the next; previously reported
spaces go first when a group grows, so their shorter old TTL cannot lapse while
new checkouts are scanned. Disable skips queued reads and joins running workers
before clearing metadata and releasing the lock. A space's
`worktree.checkout_path` wins over its first pane's cwd, so a pane changing
directories cannot mislabel a linked worktree. Ordinary spaces use the first
pane's cwd, not the transient `foreground_cwd`. Git runs read-only with
`git --no-optional-locks status --porcelain=v2 -z --branch --ahead-behind --untracked-files=all`.
Ignored files do not count. A path changed both in the index and working tree
counts once; conflicts are included in the changed-path summary too. Renames
count their destination once, skipping the separate original-path record;
filenames containing spaces, newlines, or status-like text are safe.
Ahead/behind is read from Git's local upstream metadata; no fetch is performed.
A read-only `git rev-parse --absolute-git-dir` resolves the per-worktree operation
state directory, including when a pane's cwd is a subdirectory. Its marker files
and directories detect paused operations even after conflicts are resolved. Both
Git commands share the original five-second timeout budget. Summary routing uses
the snapshot's `worktree.is_linked_worktree` flag on the publishing thread, not
path guesses or worker-thread calls.

Tokens expire after 15 seconds if reporting stops for groups of up to four
spaces. Larger groups use a finite adaptive TTL: five seconds of refresh delay,
five seconds per batch of four Git reads, five seconds of slack, plus five seconds
per snapshot/report RPC, capped at 24 hours. Thus normal four-space groups refresh
within ten seconds even when a Git read takes its full timeout, while larger groups
do not lose healthy metadata simply because collection requires multiple batches.
These bounds assume a responsive server for small groups; server failures still
allow stale metadata to expire. A detached reporter is started
by the one-shot startup hook. A kernel-held file lock per socket prevents duplicate
reporters; state and logs live in the plugin state directory under a socket-path
hash. Signals clear tokens, transient socket failures retry, and 30 seconds without
the server stops the reporter. Startup after a server restart restores it; clients
attaching do not spawn another reporter. No PID-based killing or agent state changes.
Metadata continues to use source `git-status`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests cover porcelain parsing, unique counts, renames and newline paths, conflicts,
ignored files, detached and unborn branches, upstream synchronization and divergence,
linked worktrees, command failures and non-repositories, reporting, and lifecycle.
