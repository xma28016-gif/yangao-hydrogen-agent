import json
import re

from app.core.config import AppError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportCreate(Input):
    title: str = Field(min_length=1, max_length=200)
    target_words: int = Field(default=12000, ge=8, le=18000, strict=True)
    user_requirements: str = Field(default="", max_length=2000)


class Chapter(Input):
    chapter_id: StrictInt = 1
    title: str = Field(min_length=1, max_length=200)
    key_points: list[str] = Field(min_length=1, max_length=10)
    data_needed: list[str] = Field(max_length=30)
    suggested_words: int = Field(ge=1, le=18000, strict=True)

    @field_validator("key_points", "data_needed")
    @classmethod
    def bounded(cls, values):
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("条目不能为空或超过500字")
        return [value.strip() for value in values]


class ModelOutline(BaseModel):
    full_outline: list[Chapter] = Field(min_length=1, max_length=30)


class OutlineSave(Input):
    chapters: list[Chapter] = Field(min_length=1, max_length=30)
    version: int = Field(ge=1)


class Confirm(Input):
    version: int = Field(ge=1)


class Search(Input):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=6, ge=1, le=6)


def parse_outline(content):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    return ModelOutline.model_validate(json.loads(content))


def normalize(outline, target):
    rows = [c.model_dump() for c in outline.full_outline]
    if len(rows) > target:
        raise AppError("INVALID_OUTLINE", "目标字数不足以分配章节")
    # Positive integer largest-remainder allocation, exact even for tiny targets.
    weights = [row["suggested_words"] for row in rows]
    total = sum(weights)
    remaining = target - len(rows)
    scaled = [remaining * w for w in weights]
    words = [1 + n // total for n in scaled]
    order = sorted(range(len(rows)), key=lambda i: scaled[i] % total, reverse=True)
    for i in order[: target - sum(words)]:
        words[i] += 1
    for i, row in enumerate(rows):
        row.update(chapter_id=i + 1, suggested_words=words[i])
    notices = []
    if sum(weights) != target:
        notices.append("建议字数已按比例调平至目标字数")
    if not 8 <= len(rows) <= 13:
        notices.append("章数不在8–13，请手动调整后确认")
    if any(
        re.search(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?![A-Za-z0-9])", s)
        for row in rows
        for s in row["key_points"] + row["data_needed"]
    ):
        notices.append("论点或所需数据含数字，请核对并移除具体数值后确认")
    return rows, notices


def validate_manual(chapters, target, confirming=False):
    rows = [c.model_dump() for c in chapters]
    if sum(c["suggested_words"] for c in rows) != target:
        raise AppError("INVALID_OUTLINE", f"各章字数合计须等于目标 {target}")
    if confirming and not 8 <= len(rows) <= 13:
        raise AppError("INVALID_OUTLINE", "确认时须有8–13章")
    if confirming and any(
        re.search(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?![A-Za-z0-9])", s)
        for row in rows
        for s in row["key_points"] + row["data_needed"]
    ):
        raise AppError("INVALID_OUTLINE", "论点和所需数据不得含具体数值，请修改后确认")
    for i, row in enumerate(rows):
        row["chapter_id"] = i + 1
    return rows


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    filename: str
    status: str
    size: int
    chunks_count: int
    error_code: str | None
    created_at: float


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    target_words: int
    user_requirements: str
    outline: list[Chapter]
    notices: list[str]
    status: str
    version: int
    confirmed_at: float | None
    created_at: float


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    entity_id: str
    status: str
    progress: str
    error_code: str | None
    attempts: int
