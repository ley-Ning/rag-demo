"""OpenSandbox 代码执行工具（mcp.sandbox.execute）。

在阿里开源 OpenSandbox 的隔离沙盒里执行 AI 生成的 Python 代码：
- 资源受限（默认 1 CPU / 512Mi）、出口网络默认全禁（纯计算不需要联网）
- 单命令超时 + 沙盒整体生命周期超时双保险
- stdout/stderr/退出码结构化返回，超长输出截断
- 沙盒用完即毁（destroy），不留残留容器
"""

import logging
from datetime import timedelta
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _join_logs(logs_obj: Any) -> tuple[str, str]:
    """Execution.logs 的 stdout/stderr 拼接为纯文本"""
    def _pick(attr: str) -> str:
        messages = getattr(logs_obj, attr, None) or []
        parts = []
        for message in messages:
            text = getattr(message, "text", None)
            parts.append(str(text) if text is not None else str(message))
        return "\n".join(part for part in parts if part)

    return _pick("stdout"), _pick("stderr")


async def execute_python_code(
    code: str,
    *,
    timeout_sec: int | None = None,
) -> dict[str, Any]:
    """在一次性沙盒中执行 Python 代码，返回结构化执行结果。

    失败时抛出 RuntimeError，由调用方（gateway）转为失败 tool run。
    """
    settings = get_settings()
    code = (code or "").strip()
    if not code:
        raise ValueError("code 不能为空")
    if len(code) > settings.sandbox_max_code_chars:
        raise ValueError(
            f"代码过长（{len(code)} 字符，上限 {settings.sandbox_max_code_chars}）"
        )

    # 延迟导入：后端未部署 OpenSandbox 时其余功能不受影响
    try:
        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models.filesystem import WriteEntry
        from opensandbox.models.sandboxes import NetworkPolicy
    except ImportError as exc:
        raise RuntimeError("opensandbox SDK 未安装（pip install opensandbox）") from exc

    exec_timeout = max(5, min(int(timeout_sec or settings.sandbox_exec_timeout_sec), 300))
    connection = ConnectionConfig(
        domain=settings.open_sandbox_domain,
        api_key=settings.open_sandbox_api_key,
        use_server_proxy=True,
        request_timeout=timedelta(seconds=settings.sandbox_total_timeout_sec),
    )

    sandbox = await Sandbox.create(
        settings.sandbox_image,
        connection_config=connection,
        timeout=timedelta(seconds=settings.sandbox_total_timeout_sec),
        resource={"cpu": "1", "memory": "512Mi"},
        # 纯计算场景默认禁网，AI 生成的代码不允许触碰外网与内网
        network_policy=NetworkPolicy(default_action="deny"),
        metadata={"project": "rag-demo", "tool": "mcp.sandbox.execute"},
    )
    try:
        await sandbox.files.write_files(
            [
                WriteEntry(
                    path="/tmp/rag_exec.py",
                    data=code,
                    mode=644,
                )
            ]
        )
        execution = await sandbox.commands.run(
            f"timeout {exec_timeout}s python3 /tmp/rag_exec.py"
        )

        stdout, stderr = _join_logs(execution.logs)
        exit_code = int(getattr(execution, "exit_code", 0) or 0)
        error_payload: dict[str, Any] | None = None
        if execution.error is not None:
            error_payload = {
                "name": str(getattr(execution.error, "name", "") or ""),
                "value": str(getattr(execution.error, "value", "") or "")[:2000],
                "traceback": str(getattr(execution.error, "traceback", "") or "")[:4000],
            }

        max_chars = settings.sandbox_max_output_chars
        return {
            "exitCode": exit_code,
            "stdout": stdout[:max_chars],
            "stdoutTruncated": len(stdout) > max_chars,
            "stderr": stderr[:max_chars],
            "stderrTruncated": len(stderr) > max_chars,
            "error": error_payload,
            "timedOut": exit_code == 124,
            "execTimeoutSec": exec_timeout,
            "image": settings.sandbox_image,
        }
    finally:
        try:
            await sandbox.destroy()
        except Exception:
            logger.warning("Failed to destroy sandbox after execution (best-effort)")
