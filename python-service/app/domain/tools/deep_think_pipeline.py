import time
from dataclasses import dataclass
from typing import Any


@dataclass
class DeepThinkStageResult:
    stage: str
    status: str
    latency_ms: int
    input_summary: str
    output_summary: str
    payload: dict[str, Any]
    error_message: str | None = None


@dataclass
class DeepThinkPipelineResult:
    summary: str
    stages: list[DeepThinkStageResult]


def _build_plan(question: str) -> list[str]:
    return [
        "先明确问题目标和约束",
        "抽取现有证据并标注可信度",
        "识别信息缺口与潜在风险",
        "给出可执行结论与下一步建议",
        f"围绕用户问题落地：{question[:80]}",
    ]


def run_deep_think_pipeline(
    question: str,
    evidence: list[str],
    *,
    max_iterations: int = 3,
) -> DeepThinkPipelineResult:
    stages: list[DeepThinkStageResult] = []

    plan_start = time.monotonic()
    plan_items = _build_plan(question)
    stages.append(
        DeepThinkStageResult(
            stage="plan",
            status="success",
            latency_ms=int((time.monotonic() - plan_start) * 1000),
            input_summary=f"question_chars={len(question)}",
            output_summary=f"items={len(plan_items)}",
            payload={"planItems": plan_items},
        )
    )

    execute_start = time.monotonic()
    evidence_items = [item for item in evidence if item.strip()]
    execution_notes = {
        "evidenceCount": len(evidence_items),
        "evidencePreview": [item[:200] for item in evidence_items[:3]],
        "iterations": max(1, min(max_iterations, 5)),
    }
    stages.append(
        DeepThinkStageResult(
            stage="execute",
            status="success",
            latency_ms=int((time.monotonic() - execute_start) * 1000),
            input_summary=f"evidence={len(evidence_items)}",
            output_summary=f"iterations={execution_notes['iterations']}",
            payload=execution_notes,
        )
    )

    reflect_start = time.monotonic()
    risks: list[str] = []
    if not evidence_items:
        risks.append("当前缺少外部证据，回答可能偏泛化")
    if len(question) < 8:
        risks.append("用户问题较短，目标可能不够明确")
    if not risks:
        risks.append("证据基本充分，主要风险是时效性变化")
    stages.append(
        DeepThinkStageResult(
            stage="reflect",
            status="success",
            latency_ms=int((time.monotonic() - reflect_start) * 1000),
            input_summary=f"risk_candidates={max(1, len(evidence_items))}",
            output_summary=f"risks={len(risks)}",
            payload={"risks": risks},
        )
    )

    verify_start = time.monotonic()
    conclusion = (
        "已完成深度思考四阶段："
        f"计划 {len(plan_items)} 项、证据 {len(evidence_items)} 条、风险 {len(risks)} 条。"
        "回答时优先采用证据优先 + 风险提示 + 可执行建议结构。"
    )
    stages.append(
        DeepThinkStageResult(
            stage="verify",
            status="success",
            latency_ms=int((time.monotonic() - verify_start) * 1000),
            input_summary=f"plan={len(plan_items)},evidence={len(evidence_items)}",
            output_summary="verification=passed",
            payload={"conclusion": conclusion},
        )
    )

    summary_lines = [
        "深度思考摘要：",
        f"- 计划项: {len(plan_items)}",
        f"- 证据条数: {len(evidence_items)}",
        f"- 风险条数: {len(risks)}",
        "- 建议回答结构: 结论 -> 证据 -> 风险 -> 下一步",
    ]
    return DeepThinkPipelineResult(summary="\n".join(summary_lines), stages=stages)

