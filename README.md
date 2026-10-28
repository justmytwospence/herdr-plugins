# herdr-plugins

Small [Herdr](https://herdr.dev) plugins, one per directory. Each is installed,
enabled and bound on its own; they only share this repository and a socket client.

| Plugin | id | What it does |
|---|---|---|
| [git-status](git-status/) | `git-status` | Compact Git status (`* ↑N ↓N !`) on every space, including worktree children |
| [undo](undo/) | `herdr-undo` | Reopen the last closed pane, tab or space with its layout, directories and labels, resuming agent sessions |
| [hold-done](hold-done/) | `hold-done` | Keep finished agents in the Agents list's done slot until you release them, instead of when you look at them |
| [sort-spaces](sort-spaces/) | `sort-spaces` | Sort spaces alphabetically |

## Install

```sh
herdr plugin install justmytwospence/herdr-plugins/<plugin> --ref <commit> --yes
```

Herdr checks out the whole repository and runs the plugin from its directory, so
`lib/` is available to every plugin. For development, link a directory of a
checkout instead: `herdr plugin link ~/Projects/herdr-plugins/<plugin>`.

Requires Python 3.9+ (stdlib only), on macOS or Linux. See each plugin's README for
its minimum Herdr version, actions and key bindings.

## Layout

- `lib/herdr_rpc.py`: the shared socket API client. Plugins add `../lib` to
  `sys.path`; error replies raise `HerdrError` (a `RuntimeError`), connection
  failures raise `OSError`.
- `<plugin>/herdr-plugin.toml`, its script and `tests/`.

## Tests

```sh
scripts/test.sh
```

History: git-status, undo and sort-spaces were separate repositories
(`justmytwospence/herdr-git-status`, `herdr-undo`, `herdr-sort-spaces`); their
history is imported under their directories.
