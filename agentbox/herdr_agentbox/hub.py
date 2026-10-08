"""The AgentBox hub's public API (/api/v1), stdlib only.

Boxes come back as the hub lists them: id, name, displayName, repo, agent,
provider, status (running | paused | stopped | creating | error), agentStatus.
A box still being built is listed with a `job:<id>` id until it exists.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import config


class HubError(Exception):
    pass


def request(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 20) -> Dict[str, Any]:
    hub = config.hub()
    if not hub["url"]:
        raise HubError("no hub configured (agentbox hub set-url)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(hub["url"] + path, data=data, method=method, headers={
        "authorization": "Bearer %s" % hub["api_key"],
        "accept": "application/json",
        **({"content-type": "application/json"} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        raise HubError("%s %s: HTTP %d %s" % (method, path, e.code, str(detail)[:300]))
    except (urllib.error.URLError, OSError) as e:
        raise HubError("%s %s: %s" % (method, path, e))
    return json.loads(text) if text else {}


def boxes() -> List[Dict[str, Any]]:
    return list(request("GET", "/api/v1/boxes").get("boxes") or [])


def box(box_id: str) -> Optional[Dict[str, Any]]:
    for b in boxes():
        if b.get("id") == box_id:
            return b
    return None


def lifecycle(box_id: str, action: str) -> None:
    """start | pause | resume | stop | destroy."""
    body = {} if action == "destroy" else None
    request("POST", "/api/v1/boxes/%s/%s" % (urllib.parse.quote(box_id, safe=""), action), body, timeout=300)


def create(repo: str, agent: str, provider: str, name: str, prompt: Optional[str] = None) -> str:
    """Queue a box on the hub (its worker clones `repo`); returns the job id."""
    # startAgent: a live session to attach to even without a first prompt (the hub
    # web UI's create does the same).
    body: Dict[str, Any] = {"repoUrl": repo_url(repo), "agent": agent, "provider": provider, "name": name,
                            "startAgent": True}
    if prompt:
        body["prompt"] = prompt
    return str(request("POST", "/api/v1/boxes", body).get("jobId") or "")


def repo_url(repo: str) -> str:
    if "://" in repo or repo.startswith("git@"):
        return repo
    return "https://github.com/%s" % repo.strip("/")


def label(b: Dict[str, Any]) -> str:
    return str(b.get("displayName") or b.get("name") or b.get("id"))
