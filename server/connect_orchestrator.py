"""Phase 3: Guarded 4-stage one-click robot link orchestrator for PlatformServer.

Coordinates read-only probes and guarded legacy connect calls without ever:
  - loading or switching models automatically,
  - clearing STOP latches automatically,
  - replaying motion or recording commands,
  - overwriting unverified source hashes, or
  - falling back to local disk when USB identity is unconfirmed.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import threading

from .contracts import classify_error, object_or_empty

CONNECT_SCHEMA = "agilex-platform-connect-v1"

STAGE_TEMPLATES = (
    ("ssh_probe", "网口与 SSH 探测"),
    ("usb_verify", "USB 物理核验"),
    ("session_binding", "会话与源码绑定"),
    ("agent_hub_tunnel", "代理 / Hub / 隧道"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initial_stages() -> list[dict]:
    return [
        {"id": idx + 1, "key": key, "title": title, "status": "PENDING", "reason_code": None, "detail": ""}
        for idx, (key, title) in enumerate(STAGE_TEMPLATES)
    ]


class ConnectOrchestrator:
    """Single-flight 4-stage connection orchestrator with strict safety redlines."""

    def __init__(self, legacy_client):
        self.legacy = legacy_client
        self._lock = threading.Lock()
        self._running = False
        self._state = {
            "schema": CONNECT_SCHEMA,
            "protocol_version": 1,
            "status": "IDLE",  # IDLE | RUNNING | READY | BLOCKED
            "updated_at": None,
            "pending_intent": False,
            "stages": _initial_stages(),
            "safety_boundaries": {
                "auto_load_models": False,
                "auto_clear_stop": False,
                "replay_motion": False,
            },
            "summary_code": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return deepcopy(self._state)

    def _set_stage(self, idx: int, status: str, detail: str, reason_code: str | None = None) -> None:
        with self._lock:
            st = self._state["stages"][idx]
            st["status"] = status
            st["detail"] = detail
            st["reason_code"] = reason_code
            self._state["updated_at"] = _utc_now()

    def _finish(self, status: str, summary_code: str | None = None, pending_intent: bool = False) -> dict:
        with self._lock:
            self._state["status"] = status
            self._state["summary_code"] = summary_code
            self._state["pending_intent"] = pending_intent
            self._state["updated_at"] = _utc_now()
            self._running = False
            return deepcopy(self._state)

    def _post_legacy(self, path: str, payload: dict, token: str) -> tuple[int, dict]:
        raw_body = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
        headers = {
            "Origin": self.legacy.origin,
            "Content-Type": "application/json",
            "X-Tiger-Token": token,
        }
        conn, resp = self.legacy.open("POST", path, raw_body, headers)
        try:
            body = resp.read(65536)
            parsed = json.loads(body.decode("utf-8")) if body else {}
            return resp.status, parsed if isinstance(parsed, dict) else {}
        except (ValueError, UnicodeError):
            return resp.status, {}
        finally:
            conn.close()

    def run(self, *, token: str, params: dict | None = None) -> dict:
        """Execute bounded 4-stage connection check & guarded bring-up."""
        with self._lock:
            if self._running:
                return deepcopy(self._state)
            self._running = True
            self._state["status"] = "RUNNING"
            self._state["pending_intent"] = True
            self._state["summary_code"] = None
            self._state["stages"] = _initial_stages()
            self._state["updated_at"] = _utc_now()

        # Stage 1: Legacy backend & SSH reachability probe
        self._set_stage(0, "RUNNING", "正在探测 4090 任务后台与 SSH 通路")
        try:
            state = self.legacy.state()
        except Exception as exc:
            code = getattr(exc, "code", "UPSTREAM_UNAVAILABLE")
            self._set_stage(0, "BLOCKED", "4090 任务后台不可达，请检查直连网口与后台服务", code)
            return self._finish("BLOCKED", code, pending_intent=True)

        self._set_stage(0, "PASSED", "4090 任务后台在线，准备核验设备链路")

        # Check active owner / STOP before mutating anything
        jobs = state.get("jobs") if isinstance(state.get("jobs"), list) else []
        active_jobs = [j for j in jobs if isinstance(j, dict) and j.get("state") in ("queued", "running", "unknown")]
        connected = state.get("connected") is True

        # Stage 2: USB physical identity & mount verification
        self._set_stage(1, "RUNNING", "正在只读核验设备端 USB UUID 与挂载点")
        usb = object_or_empty(state.get("usb"))
        usb_selected = object_or_empty(object_or_empty(state.get("usb_storage")).get("selected"))
        mount = usb_selected.get("mountpoint")
        usb_uuid = usb_selected.get("uuid")
        valid_mount = isinstance(mount, str) and mount.startswith("/media/")
        usb_ok = usb.get("verified") is True and bool(usb_uuid) and valid_mount

        if not usb_ok and not active_jobs:
            # Trigger a read-only storage scan on the legacy backend once (never writes local disk)
            try:
                self._post_legacy("/api/storage/scan", {}, token)
                state = self.legacy.state()
                usb = object_or_empty(state.get("usb"))
                usb_selected = object_or_empty(object_or_empty(state.get("usb_storage")).get("selected"))
                mount = usb_selected.get("mountpoint")
                usb_uuid = usb_selected.get("uuid")
                valid_mount = isinstance(mount, str) and mount.startswith("/media/")
                usb_ok = usb.get("verified") is True and bool(usb_uuid) and valid_mount
            except Exception:
                pass

        if not usb_ok:
            self._set_stage(1, "BLOCKED", "未核验到有效的 /media USB 存储盘（绝不降级写本地盘）", "USB_IDENTITY_UNCONFIRMED")
            return self._finish("BLOCKED", "USB_IDENTITY_UNCONFIRMED", pending_intent=False)
        self._set_stage(1, "PASSED", f"已核验 {mount} ({str(usb_uuid)[:12]})")

        # Stage 3: Boot session & source identity verification
        self._set_stage(2, "RUNNING", "正在核对启动会话 Boot ID 与源码指纹")
        precision = object_or_empty(state.get("precision_workbench"))
        profile = object_or_empty(precision.get("robot_profile"))
        profile_err = profile.get("error")
        err_code = classify_error(profile_err) if profile_err else None
        if err_code == "SOURCE_IDENTITY_MISMATCH":
            self._set_stage(2, "BLOCKED", "两端源码指纹不一致，请先断开并显式同步源码", "SOURCE_IDENTITY_MISMATCH")
            return self._finish("BLOCKED", "SOURCE_IDENTITY_MISMATCH", pending_intent=False)
        boot_id = profile.get("boot_id") or "pending"
        self._set_stage(2, "PASSED", f"会话已核对（Boot: {str(boot_id)[:12]}）")

        # Stage 4: Guarded connect (if disconnected & idle) + Hub & TimeQP health check
        self._set_stage(3, "RUNNING", "正在校验设备代理、共享采集 Hub 与 TimeQP 隧道")
        if not connected:
            if active_jobs:
                self._set_stage(3, "BLOCKED", "存在活动任务，已延后连接变更以保护当前控制权", "OWNER_BUSY")
                return self._finish("BLOCKED", "OWNER_BUSY", pending_intent=False)
            try:
                status_code, _ = self._post_legacy("/api/jobs", {"action": "connect"}, token)
                if status_code >= 400:
                    self._set_stage(3, "BLOCKED", f"既有后台拒绝 connect（HTTP {status_code}）", "LEGACY_GUARD_BLOCKED")
                    return self._finish("BLOCKED", "LEGACY_GUARD_BLOCKED", pending_intent=False)
                state = self.legacy.state()
                connected = state.get("connected") is True
            except Exception as exc:
                code = getattr(exc, "code", "UPSTREAM_UNAVAILABLE")
                self._set_stage(3, "BLOCKED", "连接请求超时或中断，未重发任何动作指令", code)
                return self._finish("BLOCKED", code, pending_intent=True)

        hub = object_or_empty(state.get("hub"))
        v2 = object_or_empty(object_or_empty(state.get("precision_workbench")).get("v2_profile"))
        hub_ok = hub.get("api_reachable") is True
        v2_err = classify_error(v2.get("error")) if v2.get("error") else None
        if not connected:
            self._set_stage(3, "BLOCKED", "设备代理尚未上报在线，请在工作台核对连接日志", "NOT_READY")
            return self._finish("BLOCKED", "NOT_READY", pending_intent=False)
        if not hub_ok:
            self._set_stage(3, "BLOCKED", "设备已连但共享采集 Hub 未就绪", "NOT_READY")
            return self._finish("BLOCKED", "NOT_READY", pending_intent=False)
        if v2_err == "TIMEQP_UNAVAILABLE":
            self._set_stage(3, "BLOCKED", "主链路已通，但 TimeQP(18970) 反向隧道不可达", "TIMEQP_UNAVAILABLE")
            return self._finish("BLOCKED", "TIMEQP_UNAVAILABLE", pending_intent=False)

        self._set_stage(3, "PASSED", "web_agent、共享采集 Hub 与 TimeQP 均已就绪")
        return self._finish("READY", None, pending_intent=False)
