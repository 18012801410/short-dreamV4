"""RunningHub 平台客户端（TASK-006；契约 = AI_SPEC 附录 B，TASK-001 实测钉死）。

create → 轮询 status → outputs 三段式；参考文件经 upload 换 fileName。
Image（TASK-006）与 Video（TASK-007）Provider 共用本客户端。
密钥纪律：apiKey 进请求体 `apiKey` 字段 + Bearer 头，不进日志。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

# 错误码：AUTH=key 无效；REJECTED=create 被拒（含 433 提示词审核）；
# TASK_FAILED=任务终态 FAILED；TIMEOUT=轮询超时（已尽力 cancel）；
# UPLOAD_FAILED=文件上传失败；BAD_RESPONSE=响应结构不符；TRANSPORT=网络层失败。


class RunningHubError(Exception):
    def __init__(
        self, code: str, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class RunningHubClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
        poll_interval: float = 5.0,
        poll_timeout: float = 900.0,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )
        self._api_key = api_key
        self._sleep = sleep
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    # -- 单步接口 -----------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        host = self._client.base_url.host or ""
        headers = {"Host": host, "Authorization": f"Bearer {self._api_key}"}
        try:
            resp = self._client.post(
                path, json={"apiKey": self._api_key, **payload}, headers=headers
            )
        except httpx.HTTPError as exc:
            raise RunningHubError(
                "TRANSPORT", f"RunningHub 请求失败：{type(exc).__name__}"
            ) from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise RunningHubError(
                "BAD_RESPONSE",
                f"RunningHub 返回非 JSON（HTTP {resp.status_code}）",
                details={"body": resp.text[:500]},
            ) from exc
        return {"http_status": resp.status_code, **body}

    def create_task(self, workflow_id: str, node_info_list: list[dict]) -> str:
        body = self._post(
            "/task/openapi/create",
            {"workflowId": workflow_id, "nodeInfoList": node_info_list},
        )
        if body.get("code") != 0:
            raise RunningHubError(
                "REJECTED",
                f"任务创建被拒：{body.get('msg')}",
                details={"promptTips": body.get("promptTips"), "raw_code": body.get("code")},
            )
        task_id = (body.get("data") or {}).get("taskId")
        if not task_id:
            raise RunningHubError(
                "BAD_RESPONSE", "create 响应缺少 taskId", details={"body": str(body)[:500]}
            )
        return str(task_id)

    def status(self, task_id: str) -> str:
        body = self._post("/task/openapi/status", {"taskId": task_id})
        if body.get("code") not in (0, "0"):
            raise RunningHubError(
                "BAD_RESPONSE",
                f"status 查询失败：{body.get('msg')}",
                details={"raw_code": body.get("code")},
            )
        return str(body.get("data") or "")

    def outputs(self, task_id: str) -> list[dict[str, Any]]:
        body = self._post("/task/openapi/outputs", {"taskId": task_id})
        if body.get("code") != 0:
            raise RunningHubError(
                "BAD_RESPONSE",
                f"outputs 查询失败：{body.get('msg')}",
                details={
                    "raw_code": body.get("code"),
                    "failedReason": (body.get("data") or {}).get("failedReason")
                    if isinstance(body.get("data"), dict)
                    else None,
                },
            )
        return body.get("data") or []

    def cancel(self, task_id: str) -> None:
        self._post("/task/openapi/cancel", {"taskId": task_id})

    def upload(self, file_path: Path) -> str:
        """上传文件换 fileName（可直接填入 ComfyUI 节点输入）。

        实测（TASK-001）：本接口只认 Authorization: Bearer 头，
        form/query 传 apiKey 均报 "apiKey is required"。
        """
        host = self._client.base_url.host or ""
        headers = {"Host": host, "Authorization": f"Bearer {self._api_key}"}
        try:
            with file_path.open("rb") as fh:
                resp = self._client.post(
                    "/openapi/v2/media/upload/binary",
                    files={"file": (file_path.name, fh)},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise RunningHubError(
                "TRANSPORT", f"上传失败：{type(exc).__name__}"
            ) from exc
        body = resp.json()
        file_name = (body.get("data") or {}).get("fileName")
        if not file_name:
            raise RunningHubError(
                "UPLOAD_FAILED",
                f"上传失败：{body.get('msg') or body.get('message')}",
                details={"body": str(body)[:500]},
            )
        return str(file_name)

    def download(self, url: str) -> bytes:
        """下载产物（fileUrl 为临时链接，调用方必须立即落盘）。"""
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RunningHubError(
                "TRANSPORT", f"产物下载失败：{type(exc).__name__}"
            ) from exc
        return resp.content

    # -- 轮询 ---------------------------------------------------------------

    def wait_for_success(
        self, task_id: str, *, timeout: float | None = None, label: str = "task"
    ) -> list[dict[str, Any]]:
        """轮询至终态。FAILED 抛 TASK_FAILED（带 failedReason）；超时 cancel 后抛 TIMEOUT。"""
        deadline = time.time() + (self._poll_timeout if timeout is None else timeout)
        while True:
            state = self.status(task_id)
            print(f"  [{label}] status: {state}")
            if state == "SUCCESS":
                return self.outputs(task_id)
            if state == "FAILED":
                reason: Any = None
                try:
                    body = self._post("/task/openapi/outputs", {"taskId": task_id})
                    reason = (body.get("data") or {}).get("failedReason")
                except RunningHubError:
                    pass
                raise RunningHubError(
                    "TASK_FAILED",
                    f"任务执行失败（{label} task {task_id}）",
                    details={"failedReason": reason},
                )
            if time.time() >= deadline:
                try:
                    self.cancel(task_id)
                except RunningHubError:
                    pass  # 取消失败不掩盖超时本身
                raise RunningHubError(
                    "TIMEOUT",
                    f"轮询超时（{timeout}s），已尽力取消任务 {task_id}",
                )
            self._sleep(self._poll_interval)
