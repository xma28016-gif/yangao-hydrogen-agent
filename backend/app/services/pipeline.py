import hashlib
import json
import subprocess
import sys
import time

from app.core.config import BACKEND, AppError
from app.models.tables import Chunk, Document, Event, Report, Task
from app.schemas.contracts import normalize
from app.services.retrieval import Retrieval
from sqlalchemy import or_, select


class Pipeline:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        self.retrieval = Retrieval(store, gateway)

    def enqueue(self, s, kind, entity_id):
        active_key = f"{self.store.settings.owner_id}:{kind}:{entity_id}"
        existing = s.scalar(select(Task).where(Task.active_key == active_key))
        if existing:
            return existing
        task = Task(owner_id=self.store.settings.owner_id, kind=kind, entity_id=entity_id, active_key=active_key)
        s.add(task)
        s.flush()
        return task

    def claim(self, worker_id):
        now = time.time()
        with self.store.session(write=True) as s:
            task = s.scalar(
                select(Task)
                .where(
                    Task.owner_id == self.store.settings.owner_id,
                    or_(Task.status == "queued", (Task.status == "running") & (Task.lease_until < now)),
                )
                .order_by(Task.created_at)
                .limit(1)
            )
            if task is None:
                return None
            task.status, task.worker_id = "running", worker_id
            task.lease_until = now + self.store.settings.lease_seconds
            task.attempts += 1
            task.progress = "处理中"
            s.flush()
            return task

    def check_lease(self, s, task_id, worker_id):
        task = self.store.get(s, Task, task_id)
        if task.status != "running" or task.worker_id != worker_id or task.lease_until < time.time():
            raise AppError("TASK_LEASE_LOST", "任务执行权已移交，当前执行停止", 409)
        return task

    def heartbeat(self, task_id, worker_id):
        with self.store.session(write=True) as s:
            task = self.check_lease(s, task_id, worker_id)
            task.lease_until = time.time() + self.store.settings.lease_seconds

    def finish(self, s, task_id, worker_id):
        # Final result and task completion are committed in the SAME transaction.
        task = self.check_lease(s, task_id, worker_id)
        task.status, task.active_key, task.progress = "succeeded", None, "已完成"
        task.error_code = None

    def run(self, task, worker_id):
        try:
            if task.kind == "ingest":
                self.ingest(task, worker_id)
            elif task.kind == "outline":
                self.generate_outline(task, worker_id)
            elif task.kind == "write_full":
                from app.services.writing import Writer

                Writer(self).run(task, worker_id)
            else:
                raise AppError("UNKNOWN_TASK", "不支持此任务")
        except Exception as exc:
            code = exc.code if isinstance(exc, AppError) else "TASK_FAILED"
            with self.store.session(write=True) as s:
                row = self.store.get(s, Task, task.id)
                if row.worker_id != worker_id or row.status != "running":
                    return
                row.status, row.active_key, row.error_code = "failed", None, code
                row.progress = "处理失败，请查看错误码并重试"
                if task.kind == "ingest":
                    document = self.store.get(s, Document, task.entity_id)
                    document.status, document.error_code = "failed", code
                else:
                    report = self.store.get(s, Report, task.entity_id)
                    report.status = "paused" if task.kind == "write_full" else (
                        "draft" if not report.outline else "outline_ready")

    def ingest(self, task, worker_id):
        with self.store.session(write=True) as s:
            self.check_lease(s, task.id, worker_id)
            document = self.store.get(s, Document, task.entity_id)
            document.status = "processing"
            filename, extension = document.stored_name, document.extension
            rows = s.scalars(
                select(Chunk)
                .where(Chunk.document_id == document.id, Chunk.owner_id == self.store.settings.owner_id)
                .order_by(Chunk.ordinal)
            ).all()
        if not rows:
            path = self.store.settings.data_dir / "files" / filename
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "app.services.parse_file", str(path), extension],
                    cwd=BACKEND,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                started = time.monotonic()
                try:
                    while True:
                        try:
                            output, _ = process.communicate(timeout=0.2)
                            if process.returncode:
                                raise ValueError("PARSE_FAILED")
                            parsed = json.loads(output)
                            break
                        except subprocess.TimeoutExpired:
                            rss = subprocess.run(
                                ["ps", "-p", str(process.pid), "-o", "rss="],
                                capture_output=True,
                                text=True,
                                check=False,
                            ).stdout.strip()
                            if time.monotonic() - started > 40 or (rss and int(rss) > 512 * 1024):
                                raise ValueError("PARSER_RESOURCE_LIMIT") from None
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
            except Exception:
                raise AppError("PARSE_FAILED", "文件解析失败、类型不符或超过资源限制") from None
            if parsed.get("no_text"):
                with self.store.session(write=True) as s:
                    self.check_lease(s, task.id, worker_id)
                    document = self.store.get(s, Document, task.entity_id)
                    document.status = "no_text"
                    document.chunks_count = 0
                    self.finish(s, task.id, worker_id)
                return
            content = parsed["text"].strip()
            pieces = [content[i : i + 800] for i in range(0, len(content), 700) if content[i : i + 800].strip()]
            with self.store.session(write=True) as s:
                self.check_lease(s, task.id, worker_id)
                for i, part in enumerate(pieces):
                    s.add(
                        Chunk(
                            id=hashlib.sha256(f"{task.entity_id}:{i}".encode()).hexdigest(),
                            owner_id=self.store.settings.owner_id,
                            document_id=task.entity_id,
                            ordinal=i,
                            text=part,
                        )
                    )
        while True:
            with self.store.session() as s:
                rows = s.scalars(
                    select(Chunk)
                    .where(Chunk.document_id == task.entity_id, Chunk.owner_id == self.store.settings.owner_id)
                    .order_by(Chunk.ordinal)
                ).all()
                pending = [r for r in rows if r.embedding is None or r.fingerprint != self.retrieval.fingerprint][:10]
            if not pending:
                break
            with self.store.session(write=True) as s:
                t = self.check_lease(s, task.id, worker_id)
                t.progress = f"向量处理中，已处理 {len(rows) - len([r for r in rows if r.embedding is None or r.fingerprint != self.retrieval.fingerprint])}/{len(rows)} 段"
            vectors = self.gateway.embeddings([r.text for r in pending])
            with self.store.session(write=True) as s:
                self.check_lease(s, task.id, worker_id)
                for row, vector in zip(pending, vectors, strict=True):
                    chunk = self.store.get(s, Chunk, row.id)
                    chunk.embedding, chunk.fingerprint = vector, self.retrieval.fingerprint
        for start in range(0, len(rows), 200):
            self.retrieval.index_rows(rows[start : start + 200])
        with self.store.session(write=True) as s:
            self.check_lease(s, task.id, worker_id)
            document = self.store.get(s, Document, task.entity_id)
            document.status, document.error_code, document.chunks_count = "ready", None, len(rows)
            self.finish(s, task.id, worker_id)

    def generate_outline(self, task, worker_id):
        with self.store.session() as s:
            report = self.store.get(s, Report, task.entity_id)
            target = report.target_words
            prompt = (BACKEND / "app/services/prompts/outline.md").read_text()
            template = (BACKEND / "app/assets/ndrc-enterprise-2023.txt").read_text()
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "报告标题": report.title,
                            "目标字数": target,
                            "额外要求": report.user_requirements,
                            "领域": "氢能",
                            "风格": "正式",
                            "内置范本全文": template,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        started = time.monotonic()
        outline = self.gateway.outline(messages, task.entity_id)
        if not 8 <= len(outline.full_outline) <= 13:
            with self.store.session(write=True) as s:
                self.check_lease(s, task.id, worker_id).progress = "章数不符合要求，重新生成一次"
            outline = self.gateway.outline(messages, task.entity_id)
        rows, notices = normalize(outline, target)
        with self.store.session(write=True) as s:
            self.check_lease(s, task.id, worker_id)
            report = self.store.get(s, Report, task.entity_id)
            report.outline, report.notices, report.status = rows, notices, "outline_ready"
            report.version += 1
            self.finish(s, task.id, worker_id)
            s.add(
                Event(
                    owner_id=self.store.settings.owner_id,
                    report_id=report.id,
                    name="E1",
                    details={"chapters": len(rows), "elapsed": round(time.monotonic() - started, 3)},
                )
            )
