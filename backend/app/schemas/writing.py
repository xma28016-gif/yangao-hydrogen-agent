import json
import re
from typing import Any, Literal

from app.core.config import AppError
from pydantic import BaseModel, Field

FIELDS = {
    "total_investment": "总投资及建设投资、建设期利息、流动资金",
    "capital_ratio": "资本金比例、金额和贷款安排",
    "technology_route": "制氢技术路线与电源",
    "electrolyzer_config": "电解槽台数、规模、制氢规模和年产氢量",
    "dc_power_consumption": "直流电耗",
    "pressure_level": "加注压力等级和站内储氢压力，不是电解槽出口压力",
    "project_scope": "站点构成、各站加注能力及氢气运输方式",
    "site_area": "各站占地面积",
    "construction_period": "建设期、开工和投运时间",
    "hydrogen_cost": "制氢成本及对应电价",
    "financial": "内部收益率和投资回收期",
}


def json_value(content):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


class FactsOutput(BaseModel):
    validated_data: dict[str, Any]
    sources: dict[str, Any] = Field(default_factory=dict)
    global_terminology: list[str] = Field(default_factory=list)


def normalize_facts(value, allowed_sources):
    result = FactsOutput.model_validate(value)
    values, sources = {}, {}
    for field in FIELDS:
        raw = result.validated_data.get(field)
        source = result.sources.get(field)
        # An untraceable extracted number cannot become the shared truth baseline.
        valid = isinstance(raw, str) and bool(raw.strip()) and isinstance(source, str) and source in allowed_sources
        values[field] = raw.strip()[:3000] if valid else None
        if valid:
            sources[field] = source
    return {
        "validated_data": values,
        "sources": sources,
        "global_terminology": [term.strip()[:100] for term in result.global_terminology[:10] if term.strip()],
    }


class DraftOutput(BaseModel):
    body: str = Field(min_length=1, max_length=120000)
    summary: str = Field(max_length=3000)
    missing: list[str] = Field(default_factory=list, max_length=100)
    user_notice: str = Field(default="", max_length=3000)


class Issue(BaseModel):
    severity: Literal["CRITICAL", "WARNING"]
    quote: str = Field(min_length=1, max_length=4000)
    expected: str = Field(min_length=1, max_length=2000)
    basis: str = Field(min_length=1, max_length=100)


class AuditOutput(BaseModel):
    issues: list[Issue] = Field(default_factory=list, max_length=50)
    unverifiable: list[str] = Field(default_factory=list, max_length=100)


def validate_audit(value, body, facts):
    audit = AuditOutput.model_validate(value)
    unique = []
    seen = set()
    for issue in audit.issues:
        if issue.quote not in body:
            raise ValueError("审计原句必须逐字存在于正文")
        if issue.severity == "CRITICAL" and (
            issue.basis not in FIELDS or facts["validated_data"].get(issue.basis) is None
        ):
            raise ValueError("关键错误必须有可核对的固定事实依据")
        if issue.severity == "WARNING" and issue.basis not in {*FIELDS, "global_terminology"}:
            raise ValueError("术语提示依据错误")
        key = (issue.severity, issue.quote, issue.basis)
        if key not in seen:
            unique.append(issue.model_dump())
            seen.add(key)
    return {
        "issues": unique,
        "unverifiable": audit.unverifiable,
        "critical_count": sum(i["severity"] == "CRITICAL" for i in unique),
        "warning_count": sum(i["severity"] == "WARNING" for i in unique),
    }


def char_count(text):
    return len("".join(text.split()))


def visible_heading(number, title):
    return f"第{number}章 {title}"


def chapter_caps(title, outline):
    headings = char_count(title) + sum(char_count(visible_heading(i + 1, c["title"])) for i, c in enumerate(outline))
    raw = [c["suggested_words"] * 11 // 10 for c in outline]
    budget = 19999 - headings
    if budget < len(raw):
        raise AppError("WORD_BUDGET_EXCEEDED", "标题过长，无法为正文预留字数")
    if sum(raw) <= budget:
        return raw
    base = [max(1, budget * n // sum(raw)) for n in raw]
    while sum(base) > budget:
        i = max(range(len(base)), key=lambda j: base[j])
        base[i] -= 1
    return base


def truncate_body(body, cap):
    kept = []
    count = 0
    for char in body:
        if not char.isspace():
            if count == cap:
                break
            count += 1
        kept.append(char)
    text = "".join(kept).rstrip()
    punctuation = max(text.rfind(mark) for mark in ("。", "！", "？", "!", "?"))
    return text[: punctuation + 1] if punctuation >= 0 else text


def ending(body):
    sentences = re.findall(r"[^。！？!?]+[。！？!?]?", " ".join(body.split()))
    return sentences[-1].strip() if sentences else ""


def missing_items(body):
    return list(dict.fromkeys(re.findall(r"\[待补充[^\]\n]*\]", body)))


def validate_rewrite(previous, revised, issues):
    # All unflagged spans must survive byte-for-byte, in order.
    intervals = []
    for issue in issues:
        if issue["severity"] == "CRITICAL":
            start = previous.find(issue["quote"])
            if start >= 0:
                intervals.append((start, start + len(issue["quote"])))
    intervals.sort()
    spans = []
    end = 0
    for start, stop in intervals:
        if start > end:
            spans.append(previous[end:start])
        end = max(end, stop)
    spans.append(previous[end:])
    position = 0
    for span in spans:
        found = revised.find(span, position)
        if found < 0:
            raise ValueError("重写修改了未点名的正文")
        position = found + len(span)
    if any(item not in revised for item in missing_items(previous)):
        raise ValueError("重写不得删除或填入原待补充")


def validate_citations(body, allowed_sources):
    cites = re.findall(r"\[([^\[\]\n]{1,200}\.(?:pdf|docx|md|txt))\]", body, flags=re.IGNORECASE)
    if any(name not in allowed_sources for name in cites):
        raise ValueError("引用了当前语料中不存在的文件")


class BodyEdit(BaseModel):
    body: str = Field(min_length=1, max_length=120000)
    version: int = Field(ge=1)
