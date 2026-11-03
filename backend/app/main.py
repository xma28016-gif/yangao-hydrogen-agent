import hashlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import BACKEND, AppError, Settings
from app.models.tables import Document, Report, Task, new_id
from app.repositories.store import Store
from app.schemas.contracts import (
    Chapter,
    Confirm,
    DocumentOut,
    OutlineSave,
    ReportCreate,
    ReportOut,
    Search,
    TaskOut,
    validate_manual,
)
from app.services.model_gateway import Gateway
from app.services.pipeline import Pipeline


def create_app(settings=None, store=None, gateway=None):
    settings = settings or Settings()
    store = store or Store(settings)
    gateway = gateway or Gateway(store)
    pipeline = Pipeline(store, gateway)

    @asynccontextmanager
    async def lifespan(_):
        yield
        store.engine.dispose()

    app = FastAPI(title="研稿 · 阶段2-2", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.pipeline, app.state.gateway = store, pipeline, gateway
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    def error(code, message, status):
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(AppError)
    async def app_error(_, exc):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_, exc):
        return error("VALIDATION_ERROR", "参数格式或范围不符，请检查字段与字数上限", 422)

    @app.exception_handler(HTTPException)
    async def http_error(_, exc):
        return error(
            "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR", "请求地址不存在或不支持该操作", exc.status_code
        )

    @app.exception_handler(Exception)
    async def unknown(_, exc):
        return error("INTERNAL_ERROR", "请求未完成，请重试；错误详情未对外公开", 500)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Also protects against local DNS rebinding / cross-site form uploads.
        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            return error("INVALID_HOST", "仅允许本机访问", 403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
            return error("INVALID_ORIGIN", "拒绝跨站请求", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("sec-fetch-site") == "cross-site":
            return error("INVALID_ORIGIN", "拒绝跨站请求", 403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_file_bytes * 10 + 1024 * 1024):
            return error("UPLOAD_LIMIT", "请求超出批次大小上限", 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/")
    def home():
        return FileResponse(BACKEND / "static/index.html")

    @app.get("/docs", include_in_schema=False)
    def api_schema():
        # Self-contained JSON viewer: no third-party scripts under our strict CSP.
        return app.openapi()

    @app.get("/ui.js", include_in_schema=False)
    def ui_js():
        return FileResponse(BACKEND / "static/ui.js", media_type="text/javascript")

    @app.get("/ui.css", include_in_schema=False)
    def ui_css():
        return FileResponse(BACKEND / "static/ui.css", media_type="text/css")

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ok",
            "stage": "2-1",
            "model_configured": bool(settings.model_api_key.get_secret_value().strip()),
        }

    @app.get("/api/v1/documents")
    def documents():
        with store.session() as s:
            return {"documents": [DocumentOut.model_validate(row).model_dump() for row in store.list(s, Document)]}

    @app.post("/api/v1/documents", status_code=202)
    async def upload(files: Annotated[list[UploadFile], File()]):
        if not 1 <= len(files) <= 10:
            raise AppError("UPLOAD_LIMIT", "每批最多上传10份文件")
        created, written = [], []
        try:
            # Validate the entire batch before committing any records.
            for file in files:
                name = file.filename or ""
                if "/" in name or "\\" in name or len(name) > 200 or any(ord(c) < 32 for c in name):
                    raise AppError("INVALID_FILE", "文件名不安全或过长")
                ext = Path(name).suffix.lower()
                if ext not in {".pdf", ".docx", ".md", ".txt"}:
                    raise AppError("INVALID_FILE", "请上传 PDF、docx、Markdown 或 TXT")
                identity = new_id()
                safe = identity + ext
                path = settings.data_dir / "files" / safe
                written.append(path)
                count, digest = 0, hashlib.sha256()
                with path.open("xb") as out:
                    while part := await file.read(1024 * 1024):
                        count += len(part)
                        if count > settings.max_file_bytes:
                            raise AppError("FILE_TOO_LARGE", "单个文件最大100 MB", 413)
                        digest.update(part)
                        out.write(part)
                if count == 0:
                    raise AppError("INVALID_FILE", "文件为空")
                # Cheap signature checks in request; complete parse stays isolated.
                with path.open("rb") as uploaded:
                    prefix = uploaded.read(1024)
                if (
                    (ext == ".pdf" and not prefix.startswith(b"%PDF-"))
                    or (ext == ".docx" and not prefix.startswith(b"PK"))
                    or (
                        ext in {".txt", ".md"}
                        and (b"\x00" in prefix or prefix.startswith((b"%PDF-", b"PK", b"\x89PNG")))
                    )
                ):
                    raise AppError("INVALID_FILE", "文件实际类型与扩展名不符")
                created.append(
                    Document(
                        id=identity,
                        owner_id=settings.owner_id,
                        filename=name,
                        stored_name=safe,
                        sha256=digest.hexdigest(),
                        size=count,
                        extension=ext,
                    )
                )
            with store.session(write=True) as s:
                result = []
                for row in created:
                    s.add(row)
                    s.flush()
                    task = pipeline.enqueue(s, "ingest", row.id)
                    result.append({"document": DocumentOut.model_validate(row).model_dump(), "task_id": task.id})
            return {"documents": result}
        except Exception:
            # Only remove this request's uncommitted temporary upload files.
            for path in written:
                path.unlink(missing_ok=True)
            raise
        finally:
            for file in files:
                await file.close()

    @app.post("/api/v1/documents/{identity}/retry", status_code=202, response_model=TaskOut)
    def retry_document(identity: str):
        with store.session(write=True) as s:
            row = store.get(s, Document, identity)
            if row.status not in {"failed", "queued", "processing", "ready"}:
                raise AppError("INVALID_STATE", "仅处理失败或在途资料可重试", 409)
            task = pipeline.enqueue(s, "ingest", identity)
            if task.status == "queued":
                row.status, row.error_code = "queued", None
            return TaskOut.model_validate(task)

    @app.post("/api/v1/search")
    def search(body: Search):
        return {"hits": pipeline.retrieval.search(body.query, body.limit)}

    @app.post("/api/v1/reports", status_code=201, response_model=ReportOut)
    def create_report(body: ReportCreate):
        with store.session(write=True) as s:
            if not s.scalar(
                select(Document.id).where(Document.owner_id == settings.owner_id, Document.status == "ready").limit(1)
            ):
                raise AppError("NO_DOCUMENTS", "资料库暂无可用资料，请先上传并处理", 409)
            row = Report(owner_id=settings.owner_id, **body.model_dump())
            s.add(row)
            s.flush()
            return ReportOut.model_validate(row)

    @app.get("/api/v1/reports")
    def reports():
        with store.session() as s:
            return {"reports": [ReportOut.model_validate(row).model_dump() for row in store.list(s, Report)]}

    @app.get("/api/v1/reports/{identity}", response_model=ReportOut)
    def report(identity: str):
        with store.session() as s:
            return ReportOut.model_validate(store.get(s, Report, identity))

    @app.post("/api/v1/reports/{identity}/outline/generate", status_code=202, response_model=TaskOut)
    def generate(identity: str):
        if not settings.model_api_key.get_secret_value().strip():
            raise AppError("MODEL_NOT_CONFIGURED", "请先在 backend/.env 本地配置模型 Key", 409)
        with store.session(write=True) as s:
            row = store.get(s, Report, identity)
            if row.confirmed_at is not None:
                raise AppError("INVALID_STATE", "大纲已确认，本阶段不覆盖确认结果", 409)
            task = pipeline.enqueue(s, "outline", identity)
            row.status = "generating_outline"
            return TaskOut.model_validate(task)

    def editable(s, row, version):
        if row.confirmed_at is not None or row.status == "generating_outline":
            raise AppError("INVALID_STATE", "当前正在生成或已经确认，不能编辑", 409)
        if row.version != version:
            raise AppError("VERSION_CONFLICT", "大纲已变化，请重新载入后再修改", 409)

    @app.put("/api/v1/reports/{identity}/outline", response_model=ReportOut)
    def save(identity: str, body: OutlineSave):
        with store.session(write=True) as s:
            row = store.get(s, Report, identity)
            editable(s, row, body.version)
            row.outline = validate_manual(body.chapters, row.target_words)
            row.status, row.notices = "outline_ready", []
            row.version += 1
            return ReportOut.model_validate(row)

    @app.post("/api/v1/reports/{identity}/outline/confirm", response_model=ReportOut)
    def confirm(identity: str, body: Confirm):
        with store.session(write=True) as s:
            row = store.get(s, Report, identity)
            if row.status == "confirmed" and row.version == body.version:
                return ReportOut.model_validate(row)
            editable(s, row, body.version)
            validate_manual([Chapter.model_validate(ch) for ch in row.outline], row.target_words, confirming=True)
            row.status, row.confirmed_at = "confirmed", time.time()
            return ReportOut.model_validate(row)

    @app.get("/api/v1/tasks/{identity}", response_model=TaskOut)
    def task(identity: str):
        with store.session() as s:
            return TaskOut.model_validate(store.get(s, Task, identity))

    @app.get("/api/v1/budget")
    def budget():
        return gateway.budget()

    from app.api.writing import register_writing

    register_writing(app, store, pipeline, gateway)
    return app


def app_factory():
    return create_app()
