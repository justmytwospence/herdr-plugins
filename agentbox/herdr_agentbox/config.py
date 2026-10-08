"""Paths, settings, and where the hub is.

The hub's URL and API key are the AgentBox CLI's own: `relay.controlPlaneUrl` in
~/.agentbox/config.yaml and AGENTBOX_HUB_API_KEY in
~/.agentbox/control-plane/control-plane.env. An environment variable wins.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

DEFAULTS: Dict[str, Any] = {
    # How often the daemon compares the hub's boxes with herdr's spaces.
    "poll_s": 10,
    # A space the user opens offers to become a new box.
    "new_space_creates_box": True,
    "default_agent": "claude",
    # (label, provider spec) pairs offered for a new box; the first is the default.
    "providers": [["NUC (docker:hub)", "docker:hub"], ["Daytona", "daytona"]],
    "agents": ["claude", "codex", "pi", "opencode"],
}


def home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def state_dir() -> str:
    path = os.environ.get("AGENTBOX_SPACE_STATE") or home(".local", "state", "herdr-agentbox")
    os.makedirs(path, exist_ok=True)
    return path


def state_path(*parts: str) -> str:
    path = os.path.join(state_dir(), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def boxes_root() -> str:
    return os.path.join(state_dir(), "boxes")


def box_dir(box_id: str, create: bool = True) -> str:
    """The cwd of a box's pane: herdr restores a pane in it, which is how the
    daemon recognises the pane after a restart."""
    path = os.path.join(boxes_root(), box_id)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def box_of_dir(cwd: Optional[str]) -> Optional[str]:
    """The box id whose pane directory `cwd` is, else None."""
    if not cwd:
        return None
    root = os.path.realpath(boxes_root())
    path = os.path.realpath(cwd)
    if os.path.dirname(path) != root:
        return None
    return os.path.basename(path) or None


def settings() -> Dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        with open(home(".config", "herdr-agentbox", "config.json")) as f:
            out.update(json.load(f))
    except (OSError, ValueError):
        pass
    return out


def _env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def control_plane_url(config_yaml: Optional[str] = None) -> str:
    """`relay.controlPlaneUrl` from AgentBox's config, read without a YAML parser."""
    path = config_yaml or home(".agentbox", "config.yaml")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return ""
    in_relay = False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            in_relay = line.rstrip().rstrip(":") == "relay"
            continue
        m = re.match(r"^\s+controlPlaneUrl:\s*['\"]?([^'\"\s#]+)", line)
        if in_relay and m:
            return m.group(1).rstrip("/")
    return ""


def hub() -> Dict[str, str]:
    env = _env_file(home(".agentbox", "control-plane", "control-plane.env"))
    url = os.environ.get("AGENTBOX_HUB_URL") or control_plane_url()
    key = os.environ.get("AGENTBOX_HUB_API_KEY") or env.get("AGENTBOX_HUB_API_KEY", "")
    return {"url": url.rstrip("/"), "api_key": key}


def command_path() -> str:
    """How panes call this tool: the stable ~/.local/bin link when there is one."""
    link = home(".local", "bin", "agentbox-space")
    if os.path.exists(link):
        return link
    return os.path.join(PLUGIN_ROOT, "agentbox-space.py")
