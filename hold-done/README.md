# Herdr Hold Done

Herdr marks a finished agent seen as soon as its pane is focused, and the Agents
list then drops it from the done slot to idle. Walking through the list with
`next_agent` therefore reshuffles it under you. This plugin keeps a finished agent
in the done slot until you release it (or it starts new work), so looking at it
does not move it.

It sets two pane tokens (`rank`, `held`) and replaces the Agents ordering with
`rank` then most recent state change, via `agent.view.set`:

| rank | meaning |
|---|---|
| 0 | blocked |
| 1 | finished, not released (`held = "done"`) |
| 2 | working |
| 3 | idle |
| 4 | unknown |

An agent is held while its latest `completion_seq` is newer than the last one
released for that pane. Starting new work gives its next completion a new number,
so it holds again after each completion without any action.

## Actions

| Action | Effect |
|---|---|
| `release` | Release the focused agent (bind it to a key) |
| `release-all` | Release every agent |
| `sync` | Re-apply tokens and the view (runs on startup and on every agent status change) |

```toml
[[keys.command]]
key = "prefix+y"
type = "plugin_action"
command = "hold-done.release"
description = "release agent from done"
```

Show the held marker in the Agents rows with `{ token = "$held", fg = "#d7af5f" }`.

## Limits

- The view is per server and lasts until it is cleared or the server exits; the
  startup hook reapplies it. Run the plugin on every machine you connect.
- Herdr's own done/idle icon still follows its seen state; use the `$held` token
  to see which agents are held.
- Replaces `ui.agent_panel_sort` while active; `herdr` clears it with
  `agent view clear` semantics when the plugin is unlinked.

Requires Herdr 0.9.3+ and Python 3.9+ (stdlib only). Install by linking the
checkout: `herdr plugin link ~/Projects/herdr-hold-done`.
