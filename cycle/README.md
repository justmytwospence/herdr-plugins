# cycle (Agent Cycle)

Orders the Agents list so herdr's own `next_agent` (bind it to one key) cycles every
agent, across machines, while visiting what needs you first:

1. the agent you are on
2. blocked agents you have not visited since they blocked
3. finished agents Herdr still reports unseen (`done`)
4. everyone else, least recently visited first

`next_agent` from the top goes to the most urgent other agent; the agent you leave
was visited last, so it goes to the end of the round-robin.

Each server flags its own focused pane under its own token (`cycle_cur_<host>`)
and its view sorts by that token only. Herdr orders the combined list with the
selected machine's view, so the other machine's stale "current" flag is ignored. Visiting a blocked agent counts as handling it until it blocks
again, so the cycle does not keep returning to it.

It keeps hidden pane tokens (`cycle_rank`, `cycle_seen`, `cycle_cur_<host>`) and replaces
`ui.agent_panel_sort` with `agent.view.set`, reapplied by its startup hook. Run it on
every connected machine: each server records focus on its own panes.

Requires Herdr 0.9.3+ and Python 3.9+ (stdlib only).

```sh
herdr plugin install justmytwospence/herdr-plugins/cycle --ref <commit> --yes
```
