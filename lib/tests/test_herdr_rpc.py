import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import herdr_rpc  # noqa: E402


class FakeServer:
    """Answers one request on a Unix socket with a canned reply line."""

    def __init__(self, reply):
        self.dir = tempfile.mkdtemp(prefix="hr")
        self.path = os.path.join(self.dir, "s")
        self.reply = reply
        self.request = None
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(1)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.sock.accept()
        with conn:
            buf = b""
            while b"\n" not in buf:
                buf += conn.recv(4096)
            self.request = json.loads(buf)
            conn.sendall(self.reply)
        self.sock.close()


class CallTest(unittest.TestCase):
    def run_call(self, reply, **kw):
        server = FakeServer(reply)
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": server.path}):
            try:
                return herdr_rpc.call("agent.list", {"x": 1}, **kw), server
            finally:
                server.thread.join(2)

    def test_result(self):
        result, server = self.run_call(b'{"id":"a","result":{"agents":[]}}\n', request_id="t")
        self.assertEqual(result, {"agents": []})
        self.assertEqual(server.request["method"], "agent.list")
        self.assertEqual(server.request["params"], {"x": 1})
        self.assertTrue(server.request["id"].startswith("t:agent.list:"))

    def test_error_reply(self):
        with self.assertRaisesRegex(herdr_rpc.HerdrError, "agent.list: plugin_not_found: nope"):
            self.run_call(b'{"error":{"code":"plugin_not_found","message":"nope"}}\n')

    def test_error_is_runtime_error(self):
        self.assertTrue(issubclass(herdr_rpc.HerdrError, RuntimeError))

    def test_reply_too_large(self):
        with self.assertRaisesRegex(herdr_rpc.HerdrError, "larger than 16"):
            self.run_call(b'{"result":{"k":"' + b"x" * 64 + b'"}}\n', max_bytes=16)

    def test_connect_failure_is_oserror(self):
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/nonexistent/herdr.sock"}):
            with self.assertRaises(OSError):
                herdr_rpc.call("agent.list")


if __name__ == "__main__":
    unittest.main()
