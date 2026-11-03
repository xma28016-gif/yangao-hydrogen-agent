import time
import uuid

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Owned:
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Document(Owned, Base):
    __tablename__ = "documents"
    filename: Mapped[str] = mapped_column(String)
    stored_name: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)
    extension: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)


class Chunk(Owned, Base):
    __tablename__ = "chunks"
    document_id: Mapped[str] = mapped_column(String, index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)


class Report(Owned, Base):
    __tablename__ = "reports"
    title: Mapped[str] = mapped_column(String)
    target_words: Mapped[int] = mapped_column(Integer)
    user_requirements: Mapped[str] = mapped_column(Text, default="")
    outline: Mapped[list] = mapped_column(JSON, default=list)
    notices: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    truth_base: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    corpus_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ChapterRecord(Owned, Base):
    __tablename__ = 'chapters'
    report_id: Mapped[str] = mapped_column(String, index=True)
    chapter_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String)
    suggested_words: Mapped[int] = mapped_column(Integer)
    max_words: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default='waiting')
    phase: Mapped[str] = mapped_column(String, default='retrieve')
    body: Mapped[str] = mapped_column(Text, default='')
    original_body: Mapped[str] = mapped_column(Text, default='')
    summary: Mapped[str] = mapped_column(Text, default='')
    last_ending: Mapped[str] = mapped_column(Text, default='')
    retrieved: Mapped[list | None] = mapped_column(JSON, nullable=True)
    missing: Mapped[list] = mapped_column(JSON, default=list)
    user_notice: Mapped[str] = mapped_column(Text, default='')
    audit: Mapped[dict] = mapped_column(JSON, default=dict)
    rewrite_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(default=False)
    edited: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Task(Owned, Base):
    __tablename__ = "tasks"
    kind: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    active_key: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    progress: Mapped[str] = mapped_column(String, default="排队中")
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class ModelCall(Owned, Base):
    __tablename__ = "model_calls"
    report_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    step: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    month: Mapped[str] = mapped_column(String, index=True)
    charged_micro: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="reserved")
    request_id: Mapped[str] = mapped_column(String, default="-")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed: Mapped[float] = mapped_column(Float, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    validation: Mapped[str] = mapped_column(String, default="未完成")


class Event(Owned, Base):
    __tablename__ = "events"
    report_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
