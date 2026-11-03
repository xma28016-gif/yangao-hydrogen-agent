"""Real local Chinese embeddings. No remote document submission or fake fallback."""

import math
from functools import lru_cache

from app.core.config import AppError

QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def windows(text, byte_limit=400):
    # Bound all windows below the tokenizer limit without silently truncating tails.
    parts, current, size = [], [], 0
    for char in text:
        cost = len(char.encode("utf-8"))
        if size + cost > byte_limit and current:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += cost
    if current:
        parts.append("".join(current))
    return parts


@lru_cache(maxsize=2)
def encoder(model, cache):
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model, cache_dir=cache, threads=2, providers=["CPUExecutionProvider"])


class LocalEmbedding:
    def __init__(self, settings):
        self.settings = settings

    def embed(self, texts, query=False):
        if self.settings.embedding_model != "BAAI/bge-small-zh-v1.5" or self.settings.embedding_dimensions != 512:
            raise AppError("INVALID_EMBEDDING_CONFIG", "本机中文向量模型需使用已核实的512维配置")
        model = encoder(self.settings.embedding_model, str(self.settings.data_dir / "models"))
        results = []
        for text in texts:
            pieces = windows(text)
            if query:
                pieces = [QUERY_PREFIX + p for p in pieces]
            vectors = list(model.embed(pieces, batch_size=16))
            if not vectors:
                raise AppError("LOCAL_EMBEDDING_FAILED", "本机向量输入为空")
            vector = [sum(float(v[i]) for v in vectors) / len(vectors) for i in range(512)]
            norm = math.sqrt(sum(v * v for v in vector))
            if not norm or any(not math.isfinite(v) for v in vector):
                raise AppError("LOCAL_EMBEDDING_FAILED", "本机向量结果不合格")
            results.append([v / norm for v in vector])
        return results
