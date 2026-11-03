"""Persisted, deterministic chapter scheduler; the model never controls transitions."""

import time

from app.models.tables import ChapterRecord, Document, Event, Report
from app.schemas.writing import (
    FIELDS,
    DraftOutput,
    char_count,
    ending,
    missing_items,
    normalize_facts,
    truncate_body,
    validate_audit,
    validate_citations,
    validate_rewrite,
)
from sqlalchemy import select


def chapters(store, session, report_id):
    return session.scalars(
        select(ChapterRecord)
        .where(
            ChapterRecord.owner_id == store.settings.owner_id,
            ChapterRecord.report_id == report_id,
        )
        .order_by(ChapterRecord.chapter_id)
    ).all()


class Writer:
    def __init__(self, pipeline):
        self.p = pipeline
        self.store, self.gateway = pipeline.store, pipeline.gateway

    def save(self, task, worker, chapter_id=None, **changes):
        with self.store.session(write=True) as s:
            current = self.p.check_lease(s, task.id, worker)
            row = (
                self.store.get(s, ChapterRecord, chapter_id)
                if chapter_id
                else self.store.get(s, Report, task.entity_id)
            )
            for key, value in changes.items():
                setattr(row, key, value)
            if chapter_id:
                current.progress = f"第{row.chapter_id}章 · {row.status}"

    def run(self, task, worker):
        started = time.monotonic()
        with self.store.session() as s:
            report = self.store.get(s, Report, task.entity_id)
            sources = {d.filename for d in self.store.list(s, Document) if d.id in report.corpus_ids}
        if report.truth_base is None:
            hits = {}
            for label in FIELDS.values():
                for hit in self.p.retrieval.search(
                    label,
                    3,
                    document_ids=report.corpus_ids,
                    report_id=report.id,
                ):
                    hits[hit["chunk_id"]] = hit
            facts = self.gateway.json_call(
                "facts",
                {"报告标题": report.title, "字段": FIELDS, "资料": list(hits.values())},
                lambda value: normalize_facts(value, sources),
                step="提炼固定事实",
                report_id=report.id,
            )
            self.save(task, worker, truth_base=facts)
        else:
            facts = report.truth_base
        with self.store.session() as s:
            ids = [ch.id for ch in chapters(self.store, s, report.id)]
        for identity in ids:
            self.write_chapter(task, worker, identity, report, facts, sources)
        with self.store.session(write=True) as s:
            self.p.check_lease(s, task.id, worker)
            rows = chapters(self.store, s, report.id)
            row = self.store.get(s, Report, report.id)
            row.status = "completed"
            row.notices = list(dict.fromkeys([ch.user_notice for ch in rows if ch.user_notice]))
            s.add(
                Event(
                    owner_id=self.store.settings.owner_id,
                    report_id=row.id,
                    name="E2",
                    details={
                        "chapters": len(rows),
                        "body_characters": sum(char_count(ch.body) for ch in rows),
                        "needs_manual": sum(ch.status == "needs_manual" for ch in rows),
                        "rewrites": sum(ch.rewrite_count for ch in rows),
                        "truncated": sum(ch.truncated for ch in rows),
                        "missing": sum(len(ch.missing) for ch in rows),
                        "null_fields": sum(v is None for v in facts["validated_data"].values()),
                        "elapsed": round(time.monotonic() - started, 3),
                    },
                )
            )
            self.p.finish(s, task.id, worker)

    def write_chapter(self, task, worker, identity, report, facts, sources):
        while True:
            with self.store.session() as s:
                self.p.check_lease(s, task.id, worker)
                ch = self.store.get(s, ChapterRecord, identity)
                previous = [
                    row
                    for row in chapters(self.store, s, report.id)
                    if row.chapter_id < ch.chapter_id and row.phase == "done"
                ]
            if ch.phase == "done":
                return
            if ch.phase == "retrieve":
                outline = report.outline[ch.chapter_id - 1]
                hits = self.p.retrieval.search(
                    ch.title + " " + " ".join(outline["key_points"]),
                    6,
                    document_ids=report.corpus_ids,
                    report_id=report.id,
                )
                self.save(task, worker, identity, retrieved=hits, phase="draft", status="writing")
                continue
            payload = {
                "章节": report.outline[ch.chapter_id - 1],
                "报告标题": report.title,
                "额外要求": report.user_requirements,
                "固定事实": facts,
                "上限": ch.max_words,
                "已定稿摘要": [c.summary for c in previous],
                "上一章正文末句": previous[-1].last_ending if previous else "",
                "参考资料": ch.retrieved,
            }
            if ch.phase in {"draft", "rewrite", "compress"}:
                phase = ch.phase
                if phase == "compress" and char_count(ch.body) <= ch.max_words:
                    self.save(task, worker, identity, phase="audit", status="auditing")
                    continue
                if phase != "draft":
                    payload.update(
                        {
                            "模式": "重写" if phase == "rewrite" else "压缩",
                            "原稿": ch.body,
                            "审计": ch.audit,
                            "压缩目标": max(1, ch.max_words * 9 // 10),
                        }
                    )

                def validate(value, phase=phase, ch=ch):
                    draft = DraftOutput.model_validate(value)
                    validate_citations(draft.body, sources)
                    if phase == "rewrite":
                        validate_rewrite(ch.body, draft.body, ch.audit["issues"])
                    if phase == "compress" and any(marker not in draft.body for marker in missing_items(ch.body)):
                        raise ValueError("压缩丢失待补充项")
                    return draft

                draft = self.gateway.json_call(
                    "write",
                    payload,
                    validate,
                    step={"draft": "写章", "rewrite": "重写", "compress": "压缩"}[phase],
                    report_id=report.id,
                )
                body = draft.body
                truncated = ch.truncated
                if phase == "compress" and char_count(body) > ch.max_words:
                    body = truncate_body(body, ch.max_words)
                    truncated = True
                self.save(
                    task,
                    worker,
                    identity,
                    body=body,
                    # Memory always derives from the persisted final text, never a discarded tail.
                    missing=missing_items(body),
                    user_notice=draft.user_notice or ch.user_notice,
                    truncated=truncated,
                    rewrite_count=ch.rewrite_count + (phase == "rewrite"),
                    phase="audit" if phase == "compress" else "compress",
                    status="auditing" if phase == "compress" else "compressing",
                )
                continue
            if ch.phase == "audit":
                audit = self.gateway.json_call(
                    "audit",
                    {"正文": ch.body, "固定事实": facts},
                    lambda value, body=ch.body: validate_audit(value, body, facts),
                    step="审计",
                    report_id=report.id,
                )
                if audit["critical_count"] and ch.rewrite_count < 2:
                    self.save(task, worker, identity, audit=audit, phase="rewrite", status="rewriting")
                else:
                    self.save(
                        task,
                        worker,
                        identity,
                        audit=audit,
                        phase="done",
                        status="needs_manual" if audit["critical_count"] else "ready",
                        original_body=ch.body,
                        summary=ch.body[:100],
                        last_ending=ending(ch.body),
                        missing=missing_items(ch.body),
                        version=ch.version + 1,
                    )
