"""Tests run against a throwaway HOME so nothing touches real config, state or the hub."""

import os
import sys
import tempfile

if any(m == "herdr_agentbox" or m.startswith("herdr_agentbox.") for m in sys.modules):
    raise RuntimeError("herdr_agentbox was imported before the test sandbox; its paths point at the real HOME")

_HOME = tempfile.mkdtemp(prefix="herdr-agentbox-test-")
os.environ["HOME"] = _HOME
os.environ.pop("AGENTBOX_SPACE_STATE", None)
for key in ("HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "AGENTBOX_HUB_URL", "AGENTBOX_HUB_API_KEY"):
    os.environ.pop(key, None)
