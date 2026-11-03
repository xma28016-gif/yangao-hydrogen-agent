import time

from app.core.config import AppError
from app.models.tables import ChapterRecord, Document, Event, Report, Task, new_id
from app.schemas.contracts import Confirm, TaskOut
from app.schemas.writing import BodyEdit, chapter_caps, char_count, missing_items
from app.services.exporting import export_docx, modification_ratio, total_words
from app.services.writing import chapters
from fastapi.responses import FileResponse
from sqlalchemy import select


def public_chapter(ch, report):
    sources = (report.truth_base or {}).get("sources", {})
    return {
        "chapter_id": ch.chapter_id,
        "title": ch.title,
        "status": ch.status,
        "body": ch.body,
        "max_words": ch.max_words,
        "words": char_count(ch.body),
        "missing": ch.missing,
        "user_notice": ch.user_notice,
        "truncated": ch.truncated,
        "edited": ch.edited,
        "version": ch.version,
        "rewrite_count": ch.rewrite_count,
        "audit": {
            "issues": [
                {
                    "severity": i["severity"],
                    "quote": i["quote"],
                    "expected": i["expected"],
                    "source": sources.get(i["basis"], "术语表"),
                }
                for i in ch.audit.get("issues", [])
            ],
            "unverifiable": ch.audit.get("unverifiable", []),
        },
        "references": [{"filename": h["filename"], "text": h["text"]} for h in (ch.retrieved or [])],
    }


def register_writing(app, store, pipeline, gateway):
    @app.post("/api/v1/reports/{identity}/write", status_code=202, response_model=TaskOut)
    def write_report(identity: str, body: Confirm):
        if not store.settings.model_api_key.get_secret_value().strip():
            raise AppError("MODEL_NOT_CONFIGURED", "请先在后端.env配置模型Key", 409)
        with store.session(write=True) as s:
            report = store.get(s, Report, identity)
            if report.confirmed_at is None:
                raise AppError("OUTLINE_NOT_CONFIRMED", "请先确认大纲，再写全文", 409)
            if report.version != body.version:
                raise AppError("VERSION_CONFLICT", "报告已变化，请重新载入", 409)
            latest = s.scalar(
                select(Task)
                .where(Task.owner_id == store.settings.owner_id, Task.kind == "write_full", Task.entity_id == identity)
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            if latest and latest.status in {"queued", "running", "succeeded"}:
                return TaskOut.model_validate(latest)
            if report.corpus_ids is None:
                docs = [d.id for d in store.list(s, Document) if d.status == "ready"]
                if not docs:
                    raise AppError("NO_DOCUMENTS", "请先上传并处理资料", 409)
                report.corpus_ids = docs
                caps = chapter_caps(report.title, report.outline)
                for outline, cap in zip(report.outline, caps, strict=True):
                    s.add(
                        ChapterRecord(
                            owner_id=store.settings.owner_id,
                            report_id=identity,
                            chapter_id=outline["chapter_id"],
                            title=outline["title"],
                            suggested_words=outline["suggested_words"],
                            max_words=cap,
                        )
                    )
            if latest:
                latest.status, latest.error_code, latest.worker_id = "queued", None, None
                latest.progress, latest.lease_until = "从保存的检查点继续", 0
                latest.active_key = f"{store.settings.owner_id}:write_full:{identity}"
                task = latest
            else:
                task = pipeline.enqueue(s, "write_full", identity)
            report.status = "writing"
            return TaskOut.model_validate(task)

    @app.get("/api/v1/reports/{identity}/content")
    def content(identity: str):
        with store.session() as s:
            report = store.get(s, Report, identity)
            rows = chapters(store, s, identity)
            task = s.scalar(
                select(Task)
                .where(Task.owner_id == store.settings.owner_id, Task.entity_id == identity, Task.kind == "write_full")
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            return {
                "status": report.status,
                "task": TaskOut.model_validate(task).model_dump() if task else None,
                "words": total_words(report, rows),
                "notices": report.notices,
                "chapters": [public_chapter(ch, report) for ch in rows],
                "current_chapter": next((ch.chapter_id for ch in rows if ch.phase != "done"), None),
            }

    @app.put("/api/v1/reports/{identity}/chapters/{number}")
    def edit_chapter(identity: str, number: int, body: BodyEdit):
        with store.session(write=True) as s:
            report = store.get(s, Report, identity)
            if report.status != "completed":
                raise AppError("REPORT_NOT_READY", "请等待全文生成完成后编辑", 409)
            rows = chapters(store, s, identity)
            ch = next((row for row in rows if row.chapter_id == number), None)
            if ch is None:
                raise AppError("NOT_FOUND", "章节不存在", 404)
            if ch.version != body.version:
                raise AppError("VERSION_CONFLICT", "正文已变化，请重新载入后修改", 409)
            if char_count(body.body) > ch.max_words:
                raise AppError("WORD_BUDGET_EXCEEDED", f"本章正文最多{ch.max_words}字", 409)
            ch.body = body.body
            if total_words(report, rows) >= 20000:
                raise AppError("WORD_BUDGET_EXCEEDED", "含标题及引用的全文须小于20000字", 409)
            ch.edited, ch.version, ch.missing = True, ch.version + 1, missing_items(ch.body)
            # Audit remains labelled as generation-time; saving is not a fresh model audit.
            return public_chapter(ch, report)

    @app.get("/api/v1/reports/{identity}/export")
    def download(identity: str):
        with store.session(write=True) as s:
            report = store.get(s, Report, identity)
            if report.status != "completed":
                raise AppError("REPORT_NOT_READY", "请等待全文完成后导出", 409)
            rows = chapters(store, s, identity)
            path = store.settings.data_dir / "exports" / f"{new_id()}.docx"
            export_docx(report, rows, path)
            s.add(
                Event(
                    owner_id=store.settings.owner_id,
                    report_id=identity,
                    name="E3",
                    details={
                        "action": "export_created",
                        "at": time.time(),
                        "characters": total_words(report, rows),
                        "modification_ratio": modification_ratio(
                            "\n".join(ch.original_body for ch in rows), "\n".join(ch.body for ch in rows)
                        ),
                    },
                )
            )
        return FileResponse(
            path,
            filename=f"研稿-{identity[:8]}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
