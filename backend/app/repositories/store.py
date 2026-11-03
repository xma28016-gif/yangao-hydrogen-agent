from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from app.core.config import BACKEND, AppError
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session


class Store:
    def __init__(self, settings, migrate=True):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "files").mkdir(exist_ok=True)
        self.url = f"sqlite:///{settings.data_dir / 'app.db'}"
        self.engine = create_engine(self.url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def pragmas(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")

        if migrate:
            config = Config(str(BACKEND / "alembic.ini"))
            config.set_main_option("script_location", str(BACKEND / "alembic"))
            config.set_main_option("sqlalchemy.url", self.url)
            command.upgrade(config, "head")

    @contextmanager
    def session(self, write=False):
        with Session(self.engine, expire_on_commit=False) as session:
            if write:
                session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def get(self, session, model, key):
        row = session.scalar(select(model).where(model.id == key, model.owner_id == self.settings.owner_id))
        if row is None:
            raise AppError("NOT_FOUND", "记录不存在", 404)
        return row

    def list(self, session, model):
        return session.scalars(
            select(model).where(model.owner_id == self.settings.owner_id).order_by(model.created_at.desc())
        ).all()
