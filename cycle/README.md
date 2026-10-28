# cycle (Agent Cycle)

Orders the Agents list so herdr's own `next_agent` (bind it to one key) cycles every
agent, across machines, while visiting what needs you first:

1. blocked agents you have not visited since they blocked
2. finished agents Herdr still reports unseen (`done`)
3. everyone else, least recently visited first

The agent you are on was visited last, so it sits at the bottom and `next_agent`
wraps to the top. Visiting a blocked agent counts as handling it until it blocks
again, so the cycle does not keep returning to it.

It keeps two hidden pane tokens (`cycle_rank`, `cycle_seen`) and replaces
`ui.agent_panel_sort` with `agent.view.set`, reapplied by its startup hook. Run it on
every connected machine: each server records focus on its own panes.

Requires Herdr 0.9.3+ and Python 3.9+ (stdlib only).

```sh
herdr plugin install justmytwospence/herdr-plugins/cycle --ref <commit> --yes
```
