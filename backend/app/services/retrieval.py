import hashlib
import json
from functools import lru_cache

from app.core.config import AppError
from app.models.tables import Chunk, Document
from sqlalchemy import select


@lru_cache(maxsize=8)
def collection(path, fingerprint):
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(
        "chunks-" + fingerprint[:24], embedding_function=None, metadata={"hnsw:space": "cosine"}
    )


class Retrieval:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        settings = store.settings
        self.fingerprint = hashlib.sha256(
            json.dumps(
                [
                    settings.embedding_provider,
                    settings.embedding_model,
                    settings.embedding_dimensions,
                    settings.model_base_url if settings.embedding_provider == "bailian" else "byte-windows-400-mean-v1",
                    "800-100-v1",
                ]
            ).encode()
        ).hexdigest()

    @property
    def index(self):
        return collection(str(self.store.settings.data_dir / "chroma"), self.fingerprint)

    def index_rows(self, rows):
        if rows:
            self.index.upsert(
                ids=[r.id for r in rows],
                embeddings=[r.embedding for r in rows],
                metadatas=[{"owner_id": r.owner_id, "document_id": r.document_id} for r in rows],
            )

    def search(self, query, limit=6, *, document_ids=None, report_id=None):
        with self.store.session() as s:
            docs = {d.id: d for d in self.store.list(s, Document)
                    if d.status == "ready" and (document_ids is None or d.id in document_ids)}
            if not docs:
                raise AppError("NO_DOCUMENTS", "请先上传并处理资料", 409)
            rows = s.scalars(
                select(Chunk).where(
                    Chunk.owner_id == self.store.settings.owner_id,
                    Chunk.document_id.in_(docs),
                    Chunk.fingerprint == self.fingerprint,
                )
            ).all()
        if not rows:
            raise AppError("INDEX_REBUILD_REQUIRED", "索引配置已改变，请重新处理资料", 409)
        # Repair the derived index using persisted vectors; never re-embed on read.
        for start in range(0, len(rows), 200):
            self.index_rows(rows[start : start + 200])
        vector = self.gateway.embeddings([query], query=True, report_id=report_id)[0]
        matches = self.index.query(
            query_embeddings=[vector],
            n_results=min(limit, len(rows)),
            where={"$and": [{"owner_id": self.store.settings.owner_id}, {"document_id": {"$in": list(docs)}}]},
            include=["distances"],
        )
        by_id = {r.id: r for r in rows}
        hits = []
        for chunk_id, distance in zip(matches["ids"][0], matches["distances"][0], strict=True):
            row = by_id.get(chunk_id)
            if row is not None:
                hits.append(
                    {
                        "filename": docs[row.document_id].filename,
                        "text": row.text,
                        "score": round(1 - distance, 6),
                        "chunk_id": row.id,
                    }
                )
        return hits
