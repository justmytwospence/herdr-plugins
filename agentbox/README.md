# herdr-agentbox

A [herdr](https://herdr.dev) plugin that gives every [AgentBox](https://github.com/madarco/agentbox)
box its own herdr space: one view of every box the hub owns, wherever it runs, with the box
agent's live state in herdr's sidebar like a local agent.

- **One space per box.** A sync daemon (the plugin's startup hook) compares the hub's boxes
  with herdr's spaces every 10 seconds: a new box gets a space, a destroyed box's space
  closes, a space herdr restored after a restart gets its pane back.
- **The pane is the agent.** While the box runs, the pane is
  `agentbox <agent> attach --inline <box>` (AgentBox's footer, approvals, and the agent's
  working/idle/needs-input reported to the pane), starting the agent's session if it has
  none. Paused or stopped: Enter resumes it; resumed anywhere else (web UI, phone), it
  attaches on its own; paused anywhere else, the pane drops back to the paused screen.
  Detached (`Ctrl-a d`): Enter reattaches, `s` opens a shell in the box, `q` parks the pane
  in a plain shell (`agentbox-space box` there comes back).
- **A new space makes a new box.** Opening a space asks for a repo (your boxes' repos, then
  `gh repo list`, or type `owner/repo`), an agent and where (the hub's own engine or
  Daytona), queues the box on the hub and attaches when it is up. Esc keeps a plain shell.
- **Actions:** pause, resume, destroy (twice to confirm) the focused box; sync now; status.

Install it in the herdr server that should own the view, ideally an always-on one (the
author's runs on a homelab NUC beside the hub, and laptops see its spaces as a herdr
machine). It needs, on that host:

- the `agentbox` CLI at the hub's version, pointed at the hub (`agentbox hub set-url`,
  `~/.agentbox/control-plane/control-plane.env` with `AGENTBOX_HUB_API_KEY`), **with its
  optional native pty module**: without `@homebridge/node-pty-prebuilt-multiarch`,
  `agentbox attach` falls back to a plain `docker exec` and reports no agent state. With an
  npm that gates install scripts:
  `npm install -g @madarco/agentbox@<version> --allow-scripts=@homebridge/node-pty-prebuilt-multiarch`.
  The module declares Node `<25`; on a newer Node npm skips it silently.
- a way for that CLI to reach each box's engine. For `docker:<host>` boxes it is ssh, even
  when the engine is this machine: register the hub's engine alias locally
  (`agentbox remote-docker add hub <ssh-alias> --no-share --no-bake`).
- python3 (3.9+); `fzf` and `gh` make the new-space picker nicer.

Settings, all optional, in `~/.config/herdr-agentbox/config.json`: `poll_s` (10),
`new_space_creates_box` (true), `default_agent`, `agents`, `providers`
(`[[label, provider spec], ...]`, default the hub's engine then Daytona).

State and logs: `~/.local/state/herdr-agentbox/` (`boxes/<id>` is each box pane's
directory, which is how panes are found again after a herdr restart; `logs/daemon.log`).

`agentbox-space status` lists the boxes and their spaces; `agentbox-space sync` runs one
daemon pass.

Tests: `python3 -B -m unittest discover -s tests -t .`

MIT licensed.
