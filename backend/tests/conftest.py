import json
import math
from types import SimpleNamespace as NS

import pytest
from app.core.config import Settings
from app.main import create_app
from app.models.tables import Document
from app.repositories.store import Store
from app.services.model_gateway import Gateway
from fastapi.testclient import TestClient
from pydantic import SecretStr


def outline(count=10, weight=1000):
    return {
        "full_outline": [
            {
                "chapter_id": i + 1,
                "title": title,
                "key_points": ["说明建设要求", "分析实施条件"],
                "data_needed": ["总投资", "建设工期"],
                "suggested_words": weight,
            }
            for i, title in enumerate(
                [
                    "概述",
                    "项目建设背景和必要性",
                    "项目市场分析",
                    "项目选址与要素保障",
                    "项目建设方案",
                    "项目运营方案",
                    "项目投融资与财务方案",
                    "项目影响效果分析",
                    "项目风险管控方案",
                    "研究结论及建议",
                    "附表附图和附件",
                    "补充分析",
                    "补充建议",
                    "超额章节",
                ][:count]
            )
        ]
    }


class FakeClient:
    """Offline-only transport substitute. Never selected by runtime settings."""

    def __init__(self, count=10):
        self.count, self.calls = count, []
        self.embeddings = NS(create=self.embed)
        self.chat = NS(completions=NS(create=self.chat_create))

    def embed(self, **kwargs):
        self.calls.append(("embedding", kwargs))
        vectors = []
        for text in kwargs["input"]:
            vector = [0.01] * kwargs["dimensions"]
            for c in text:
                vector[ord(c) % len(vector)] += 1
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append([v / norm for v in vector])
        return NS(
            data=[NS(index=i, embedding=v) for i, v in enumerate(vectors)],
            usage=NS(prompt_tokens=sum(len(t) for t in kwargs["input"])),
            _request_id="offline-embedding",
        )

    def chat_create(self, **kwargs):
        self.calls.append(("chat", kwargs))
        return NS(
            choices=[NS(message=NS(content=json.dumps(outline(self.count), ensure_ascii=False)))],
            usage=NS(prompt_tokens=200, completion_tokens=300),
            id="offline-outline",
        )

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        model_api_key=SecretStr("fake-for-test"),
        embedding_dimensions=64,
        model_retries=3,
        model_provider="bailian",
        model_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen3.7-plus-2026-05-26",
        embedding_provider="bailian",
        embedding_model="text-embedding-v4",
    )
    store = Store(settings)
    client = FakeClient()
    gateway = Gateway(store, client_factory=lambda: client, sleep=lambda _: None)
    app = create_app(settings, store, gateway)
    with TestClient(app, raise_server_exceptions=False) as api:
        yield NS(
            settings=settings, store=store, fake=client, gateway=gateway, app=app, api=api, pipeline=app.state.pipeline
        )


def ready_document(env):
    with env.store.session(write=True) as s:
        row = Document(
            owner_id=env.settings.owner_id,
            filename="独立测试资料.txt",
            stored_name="test.txt",
            sha256="offline",
            size=5,
            extension=".txt",
            status="ready",
            chunks_count=1,
        )
        s.add(row)
        s.flush()
        return row.id


def make_report(env):
    ready_document(env)
    response = env.api.post("/api/v1/reports", json={"title": "独立测试项目可研报告"})
    assert response.status_code == 201, response.text
    return response.json()


def run_pending(env):
    while task := env.pipeline.claim("offline-worker"):
        env.pipeline.run(task, "offline-worker")
