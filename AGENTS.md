# herdr-plugins

Small Herdr plugins, one per directory, sharing `lib/herdr_rpc.py`. Keep each plugin
independently installable: its own manifest, id, README and tests; no imports from a
sibling plugin, only from `lib/`. Changing `lib/` affects every plugin, so run all tests.

## Checks

`scripts/test.sh` (stdlib unittest, no live server). For live checks, link the
plugin directory (`herdr plugin link ~/Projects/herdr-plugins/<plugin>`, after
`herdr plugin uninstall <id>` if a GitHub copy is installed) and invoke its actions.

## Release

One commit versions every plugin. Commit (conventional commits) and push, then bump
the refs of the plugins pinned in `~/Projects/dotfiles/home/.chezmoidata/herdr_plugins.json`
to that full commit, following `~/Projects/dotfiles/docs/plugins.md` (converge
installs it; sync the NUC per dotfiles' AGENTS.md). Plugin ids must not change:
key bindings and plugin config/state directories are keyed by them.
