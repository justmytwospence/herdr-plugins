# cycle (Agent Cycle)

Orders the Agents list so herdr's own `next_agent` (bind it to one key) cycles every
agent, across machines, while visiting what needs you first:

1. the agent you are on
2. blocked agents you have not visited since they blocked
3. agents whose prompt cache expires soon, soonest first, that you have not been on
   since (needs pi-cache-guard; see below)
4. finished agents Herdr still reports unseen (`done`)
5. idle agents, least recently visited first
6. working agents, least recently visited first
7. agents whose turn only waits on background work (see below), least recently
   visited first

A working agent needs nothing from you until it stops (it then comes back as done or
blocked), so it never goes ahead of an agent waiting for input.

`next_agent` from the top goes to the most urgent other agent; the agent you leave
was visited last, so it goes to the end of the round-robin.

Each server flags its own focused pane under its own token (`cycle_cur_<host>`)
and its view sorts by that token only. Herdr orders the combined list with the
selected machine's view, so the other machine's stale "current" flag is ignored. Visiting a blocked agent counts as handling it until it blocks
again, so the cycle does not keep returning to it.

Switching machines into a server whose focused pane is already the one you land on
(always so for a machine with a single agent) fires no focus event there, so a turn
starting on a server's focused pane (idle, done or blocked to working) also counts as
a visit, and so does Herdr clearing an agent's `done` (it does so only once you have
seen it). Without them that agent would never count as visited and would keep the
front of the round-robin. Landing on it while it works, without starting a turn,
still goes unrecorded: that is why working agents rank after idle ones.

## Skipping a repo's spaces

The `next-block` action focuses the next agent in the same queue order that is outside
the current agent's block: a repository's spaces (its main checkout and its linked
worktrees) form one block, and any other space is a block of its own. Bind it next to
`next_agent` to step over the rest of a repo's worktree spaces in one press:

```toml
[[keys.command]]
key = "prefix+ctrl+enter"
type = "plugin_action"
command = "agent-cycle.next-block"
```

It sees the agents of the server it runs on (the machine you are viewing), not every
connected machine as `next_agent` does. When every agent shares one block it goes to
the next agent.

## Expiring prompt caches

With [pi-cache-guard](https://github.com/justmytwospence/pi-plugins/tree/main/packages/pi-cache-guard)
(through pi-herdr), an idle pi session whose warm prompt cache is big enough to warn
about (`warn.minTokens`) reports `cache_expires` in its last `herdr.expiringMinutes`
(default 10): the expiry as epoch ms, dropped by Herdr when it passes. The view sorts
on that token directly, so the agent moves up as soon as it appears, with no event
needed. Being on the agent while it is reported (landing on it, or being on it when it
appears and then leaving) takes it back out, until a new turn reports a new expiry.
The window and size threshold are cache-guard's settings. Without cache-guard the
token never appears and the order is as above without step 3.

## Agents waiting on background work

A working pi session whose turn only waits on a background job (pi-bg's `bg_wait`, or
its wait for a job when the agent ends its turn) carries pi-herdr's `wait` pane token.
It needs nothing from you until the job ends, so it goes to the end of the queue
however long ago you visited it, and comes back to its place when the wait ends.
Token changes invoke no plugin hook, so the startup hook also starts a watcher: one
detached `cycle.py watch` per server, subscribed to `pane.updated` over the socket,
that re-syncs only when a pane's wait starts or ends and exits when the server closes
the connection. After installing a new version, run the `sync` action (or restart
Herdr) to replace the running watcher. Without pi-herdr and pi-bg the token never
appears and the order is as above without step 7.

## Tokens

It keeps hidden pane tokens (`cycle_blocked`, `cycle_cache_ack`, `cycle_rank`,
`cycle_seen`, `cycle_cur_<host>`) and replaces
`ui.agent_panel_sort` with `agent.view.set`, reapplied by its startup hook. Run it on
every connected machine: each server records focus on its own panes.

Requires Herdr 0.9.3+ and Python 3.9+ (stdlib only).

```sh
herdr plugin install justmytwospence/herdr-plugins/cycle --ref <commit> --yes
```
