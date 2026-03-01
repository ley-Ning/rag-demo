import re
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.config import get_settings
from app.domain.mcp.gateway import ToolInvokeResult, get_mcp_gateway
from app.domain.mcp.registry import ensure_builtin_tools, get_mcp_tool
from app.domain.tools.deep_think_pipeline import DeepThinkStageResult, run_deep_think_pipeline

settings = get_settings()
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass
class ToolSkillCall:
    skill_name: str
    status: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    input_summary: str = ""
    output_summary: str = ""
    error_message: str | None = None


@dataclass
class ToolRunRecord:
    tool_name: str
    source: str
    status: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_summary: str
    output_summary: str
    output_payload: dict[str, Any]
    error_message: str | None = None


@dataclass
class DeepThinkRunRecord:
    stage: str
    status: str
    latency_ms: int
    input_summary: str
    output_summary: str
    payload: dict[str, Any]
    error_message: str | None = None


@dataclass
class ToolOrchestrationResult:
    rewritten_question: str
    skill_calls: list[ToolSkillCall]
    tool_runs: list[ToolRunRecord]
    deep_think_summary: str | None
    deep_think_runs: list[DeepThinkRunRecord]
    web_sources: list[dict[str, Any]]


def _extract_urls(question: str) -> list[str]:
    urls = URL_PATTERN.findall(question)
    deduped: list[str] = []
    for raw in urls:
        url = raw.strip().rstrip(".,;)")
        if url and url not in deduped:
            deduped.append(url)
    return deduped


def _should_try_web_tool(question: str) -> bool:
    lowered = question.lower()
    keywords = ("网页", "网站", "链接", "url", "http://", "https://", "看看", "查看")
    return any(word in lowered for word in keywords)


def _to_skill_call(run: ToolInvokeResult) -> ToolSkillCall:
    return ToolSkillCall(
        skill_name=run.tool_name,
        status=run.status,
        latency_ms=run.latency_ms,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_summary=run.input_summary,
        output_summary=run.output_summary,
        error_message=run.error_message,
    )


def _to_tool_run(run: ToolInvokeResult) -> ToolRunRecord:
    return ToolRunRecord(
        tool_name=run.tool_name,
        source=run.source,
        status=run.status,
        latency_ms=run.latency_ms,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        input_summary=run.input_summary,
        output_summary=run.output_summary,
        output_payload=run.output_payload,
        error_message=run.error_message,
    )


def _to_deep_run(stage: DeepThinkStageResult) -> DeepThinkRunRecord:
    return DeepThinkRunRecord(
        stage=stage.stage,
        status=stage.status,
        latency_ms=stage.latency_ms,
        input_summary=stage.input_summary,
        output_summary=stage.output_summary,
        payload=stage.payload,
        error_message=stage.error_message,
    )


class ToolOrchestrator:
    async def orchestrate(
        self,
        conn: asyncpg.Connection | None,
        *,
        question: str,
        trace_id: str,
        enable_tools: bool,
        enable_deep_think: bool,
        max_tool_steps: int,
    ) -> ToolOrchestrationResult:
        if conn is not None:
            await ensure_builtin_tools(conn)

        skill_calls: list[ToolSkillCall] = []
        tool_runs: list[ToolRunRecord] = []
        deep_think_summary: str | None = None
        deep_think_runs: list[DeepThinkRunRecord] = []
        web_sources: list[dict[str, Any]] = []
        evidence: list[str] = []

        urls = _extract_urls(question)
        should_try_web = bool(urls) or _should_try_web_tool(question)
        max_steps = max(1, min(int(max_tool_steps or settings.mcp_max_steps), 12))

        if enable_tools and should_try_web and conn is not None:
            web_tool = await get_mcp_tool(conn, "mcp.web.fetch")
            if web_tool and web_tool.enabled and max_steps > 0:
                gateway = get_mcp_gateway()
                candidate_urls = urls[:max_steps] if urls else []
                # 用户没给 URL 但表达了“查看网页”，先不盲目抓全网，提示用户给 URL
                if not candidate_urls:
                    skill_calls.append(
                        ToolSkillCall(
                            skill_name="mcp.web.fetch",
                            status="failed",
                            latency_ms=0,
                            input_summary="url=missing",
                            output_summary="",
                            error_message="未检测到可抓取的 URL，请在问题中提供 http/https 链接",
                        )
                    )
                for url in candidate_urls:
                    try:
                        invoke_result = await gateway.invoke(
                            conn,
                            tool_name="mcp.web.fetch",
                            args={"url": url, "maxChars": settings.mcp_web_max_content_chars},
                            trace_id=trace_id,
                        )
                        skill_calls.append(_to_skill_call(invoke_result))
                        tool_runs.append(_to_tool_run(invoke_result))
                        if invoke_result.status == "success":
                            source = {
                                "url": invoke_result.output_payload.get("url", url),
                                "title": invoke_result.output_payload.get("title", ""),
                                "excerpt": invoke_result.output_payload.get("excerpt", ""),
                            }
                            web_sources.append(source)
                            evidence.append(
                                f"URL: {source['url']}\n标题: {source['title']}\n摘要: {source['excerpt'][:1200]}"
                            )
                    except Exception as exc:
                        error_msg = str(exc)
                        skill_calls.append(
                            ToolSkillCall(
                                skill_name="mcp.web.fetch",
                                status="failed",
                                latency_ms=0,
                                input_summary=f"url={url}",
                                output_summary="",
                                error_message=error_msg,
                            )
                        )
                        tool_runs.append(
                            ToolRunRecord(
                                tool_name="mcp.web.fetch",
                                source="builtin",
                                status="failed",
                                latency_ms=0,
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                input_summary=f"url={url}",
                                output_summary="",
                                output_payload={},
                                error_message=error_msg,
                            )
                        )

        if enable_deep_think:
            deep_result = run_deep_think_pipeline(
                question,
                evidence,
                max_iterations=settings.deep_think_max_iterations,
            )
            deep_think_summary = deep_result.summary
            deep_think_runs = [_to_deep_run(stage) for stage in deep_result.stages]
            skill_calls.append(
                ToolSkillCall(
                    skill_name="mcp.deep_think.pipeline",
                    status="success",
                    latency_ms=sum(item.latency_ms for item in deep_think_runs),
                    input_summary=f"evidence={len(evidence)}",
                    output_summary=f"stages={len(deep_think_runs)}",
                )
            )

        rewritten_question = question
        if web_sources:
            context_lines = ["\n[网页插件证据]"]
            for idx, source in enumerate(web_sources, start=1):
                context_lines.append(
                    f"[web-{idx}] {source.get('title', '')}\n{source.get('url', '')}\n{source.get('excerpt', '')[:1800]}"
                )
            rewritten_question = f"{rewritten_question}\n\n" + "\n\n".join(context_lines)
        if deep_think_summary:
            rewritten_question = (
                f"{rewritten_question}\n\n[深度思考框架]\n{deep_think_summary}\n"
                "请按“结论 -> 证据 -> 风险 -> 下一步”结构回答。"
            )

        return ToolOrchestrationResult(
            rewritten_question=rewritten_question,
            skill_calls=skill_calls,
            tool_runs=tool_runs,
            deep_think_summary=deep_think_summary,
            deep_think_runs=deep_think_runs,
            web_sources=web_sources,
        )


_orchestrator: ToolOrchestrator | None = None


def get_tool_orchestrator() -> ToolOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ToolOrchestrator()
    return _orchestrator

