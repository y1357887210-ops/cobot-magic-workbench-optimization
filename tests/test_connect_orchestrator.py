"""Unit tests for Phase 3 guarded 4-stage ConnectOrchestrator and /platform/connect."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server.main import PlatformServer

BOOT = "01234567-89ab-cdef-0123-456789abcdef"


def base_state():
    return {
        "connected": True,
        "capabilities": {"connect": {"available": False, "reason": "已连接 AgileX"}},
        "jobs": [],
        "hub": {"api_reachable": True, "recording": {"active": False, "saving": False}},
        "usb": {"verified": True},
        "usb_storage": {"selected": {"uuid": "TEST-USB-UUID", "mountpoint": "/media/agilex/USB0", "available_bytes": 4096}},
        "precision_workbench": {
            "fresh": True,
            "models": {
                "FP32": {"status": "STOPPED", "ready": False, "busy": False, "lease_active": False},
                "BF16": {"status": "STOPPED", "ready": False, "busy": False, "lease_active": False},
            },
            "robot_profile": {"ready": True, "boot_id": BOOT},
            "v2_profile": {"ready": True, "fresh": True, "acceptance": {"validated": False}},
        },
    }


class MockLegacyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.server.offline:
            self.connection.close()
            return
        raw = json.dumps(self.server.state).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
        self.server.posts.append((self.path, body))
        if self.path == "/api/jobs" and body.get("action") == "connect":
            self.server.state["connected"] = True
            self.server.state["hub"]["api_reachable"] = True
        raw = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ConnectOrchestratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.web = Path(cls.tmp.name) / "web"
        cls.web.mkdir()
        (cls.web / "index.html").write_text("ok")
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), MockLegacyHandler)
        cls.upstream.daemon_threads = True
        cls.upstream.state = base_state()
        cls.upstream.offline = False
        cls.upstream.posts = []
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.facade = PlatformServer(0, f"http://127.0.0.1:{cls.upstream.server_port}", cls.web, timeout=0.5, cache_ttl=0)
        threading.Thread(target=cls.facade.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.facade.shutdown()
        cls.facade.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.upstream.state = base_state()
        self.upstream.offline = False
        self.upstream.posts = []

    def post_connect(self, body=b'{"arm_mode":"dual"}', token="test-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.facade.server_port, timeout=2)
        headers = {
            "Host": f"127.0.0.1:{self.facade.server_port}",
            "Origin": f"http://127.0.0.1:{self.facade.server_port}",
            "Content-Type": "application/json",
        }
        if token is not None:
            headers["X-Tiger-Token"] = token
        conn.request("POST", "/platform/connect", body, headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, json.loads(raw)

    def test_ready_four_stages_and_safety_redlines(self):
        code, data = self.post_connect()
        self.assertEqual(code, 200)
        self.assertEqual(data["schema"], "agilex-platform-connect-v1")
        self.assertEqual(data["status"], "READY")
        self.assertTrue(all(st["status"] == "PASSED" for st in data["stages"]))
        self.assertEqual(data["safety_boundaries"], {
            "auto_load_models": False,
            "auto_clear_stop": False,
            "replay_motion": False,
        })

    def test_disconnected_idle_dispatches_guarded_connect_once(self):
        self.upstream.state["connected"] = False
        self.upstream.state["hub"]["api_reachable"] = False
        code, data = self.post_connect()
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "READY")
        self.assertIn(("/api/jobs", {"action": "connect"}), self.upstream.posts)

    def test_unverified_usb_blocks_at_stage_2_without_local_fallback(self):
        self.upstream.state["usb"]["verified"] = False
        code, data = self.post_connect()
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "BLOCKED")
        self.assertEqual(data["summary_code"], "USB_IDENTITY_UNCONFIRMED")
        self.assertEqual(data["stages"][1]["status"], "BLOCKED")

    def test_source_mismatch_blocks_at_stage_3(self):
        self.upstream.state["precision_workbench"]["robot_profile"]["error"] = "source sha mismatch"
        code, data = self.post_connect()
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "BLOCKED")
        self.assertEqual(data["summary_code"], "SOURCE_IDENTITY_MISMATCH")
        self.assertEqual(data["stages"][2]["status"], "BLOCKED")

    def test_timeqp_unavailable_blocks_at_stage_4(self):
        self.upstream.state["precision_workbench"]["v2_profile"]["error"] = "timeqp port 18970 refused"
        code, data = self.post_connect()
        self.assertEqual(code, 200)
        self.assertEqual(data["status"], "BLOCKED")
        self.assertEqual(data["summary_code"], "TIMEQP_UNAVAILABLE")
        self.assertEqual(data["stages"][3]["status"], "BLOCKED")

    def test_missing_token_rejected_with_403(self):
        code, data = self.post_connect(token=None)
        self.assertEqual(code, 403)
        self.assertEqual(data["error_code"], "TOKEN_REQUIRED")


if __name__ == "__main__":
    unittest.main()
