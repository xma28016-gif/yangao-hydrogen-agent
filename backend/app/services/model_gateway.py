import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import AppError
from app.models.tables import ModelCall
from sqlalchemy import func, select


def month_now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m")


class Gateway:
    def __init__(self, store, client_factory=None, observer=None, sleep=time.sleep, local_encoder=None):
        self.store = store
        self.settings = store.settings
        self.client_factory = client_factory
        self.observer = observer
        self.sleep = sleep
        self.local_encoder = local_encoder

    def budget(self):
        with self.store.session() as s:
            used = s.scalar(
                select(func.coalesce(func.sum(ModelCall.charged_micro), 0)).where(
                    ModelCall.owner_id == self.settings.owner_id, ModelCall.month == month_now()
                )
            )
        return {"used_yuan": used / 1_000_000, "limit_yuan": self.settings.monthly_budget_yuan}

    def reserve(self, model, step, amount, report_id, retry):
        with self.store.session(write=True) as s:
            query = select(func.coalesce(func.sum(ModelCall.charged_micro), 0)).where(
                ModelCall.owner_id == self.settings.owner_id
            )
            monthly = s.scalar(query.where(ModelCall.month == month_now()))
            report = s.scalar(query.where(ModelCall.report_id == report_id)) if report_id else 0
            if monthly + amount > round(self.settings.monthly_budget_yuan * 1_000_000) or (
                report_id and report + amount > round(self.settings.report_budget_yuan * 1_000_000)
            ):
                raise AppError("BUDGET_EXCEEDED", "费用预留将超过预算，任务已停止；不会自动追加费用", 409)
            row = ModelCall(
                owner_id=self.settings.owner_id,
                month=month_now(),
                report_id=report_id,
                model=model,
                step=step,
                charged_micro=amount,
                retries=retry,
            )
            s.add(row)
            s.flush()
            return row.id

    def invoke(self, operation, validate, *, model, step, input_bytes, output_cap=0, report_id=None, embedding=False):
        if not self.settings.model_api_key.get_secret_value().strip():
            raise AppError("MODEL_NOT_CONFIGURED", "请在后端 .env 本地配置与接口厂商匹配的 Key", 409)
        from urllib.parse import urlparse

        host = urlparse(self.settings.model_base_url).hostname or ""
        provider = self.settings.model_provider
        if (provider == "deepseek" and host != "api.deepseek.com") or (
            provider == "bailian" and not host.endswith(".aliyuncs.com")
        ):
            raise AppError("MODEL_PROVIDER_MISMATCH", "模型厂商与接口地址不匹配，已阻止发送凭据", 409)
        if embedding and provider != "bailian":
            raise AppError("EMBEDDING_PROVIDER_MISMATCH", "当前Key不能用于另一厂商的向量接口", 409)
        expected = (
            "text-embedding-v4"
            if embedding
            else ("deepseek-flash" if self.settings.model_provider == "deepseek" else "qwen3.7-plus-2026-05-26")
        )
        if model != expected and not (provider == "deepseek" and model == "deepseek-v4-pro" and not embedding):
            raise AppError("UNPRICED_MODEL", "模型与已核实的计价配置不符，需先核实单价", 409)
        if input_bytes > 180000 or output_cap > 8192:
            raise AppError("MODEL_INPUT_LIMIT", "模型输入或输出预算超出本阶段安全上限")
        input_rate, output_rate = (0.5, 0) if embedding else ((9, 27) if model == "deepseek-v4-pro" else (2, 8))
        # DeepSeek uses peak, cache-miss RMB pricing as conservative bounds.
        # Exact off-peak/cache discounts are not required to remain within budget.
        reserved = math.ceil((input_bytes + 512) * input_rate + output_cap * output_rate)
        last_error = None
        for retry in range(self.settings.model_retries + 1):
            # Construct before reservation: local initialization failures cannot be billed.
            try:
                if self.client_factory:
                    client = self.client_factory()
                else:
                    from openai import OpenAI

                    client = OpenAI(
                        api_key=self.settings.model_api_key.get_secret_value(),
                        base_url=self.settings.model_base_url,
                        timeout=self.settings.model_timeout,
                        max_retries=0,
                    )
            except Exception:
                last_error = "MODEL_INITIALIZATION_FAILED"
                if retry < self.settings.model_retries:
                    self.sleep(min(2**retry, 4))
                continue
            try:
                call_id = self.reserve(model, step, reserved, report_id, retry)
            except Exception:
                close = getattr(client, "close", None)
                if close:
                    close()
                raise
            started = time.monotonic()
            response = None
            validation = "未完成"
            non_retryable = False
            try:
                response = operation(client)
                value = validate(response)
                validation = "首测合规" if retry == 0 else "重试后合规"
                succeeded = True
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                last_error = {
                    401: "MODEL_AUTHENTICATION_FAILED",
                    402: "MODEL_BALANCE_REQUIRED",
                    403: "MODEL_ACCESS_DENIED",
                    404: "MODEL_NOT_FOUND",
                }.get(status, "MODEL_RESPONSE_FAILED")
                non_retryable = status in {401, 402, 403, 404}
                succeeded = False
                value = None
                validation = "失败（" + last_error + "）"
            finally:
                close = getattr(client, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass
                with self.store.session(write=True) as s:
                    call = self.store.get(s, ModelCall, call_id)
                    usage = getattr(response, "usage", None)
                    tin = getattr(usage, "prompt_tokens", None) if usage else None
                    tout = getattr(usage, "completion_tokens", 0 if embedding else None) if usage else None
                    if tin is not None and tout is not None and tin >= 0 and tout >= 0:
                        call.input_tokens, call.output_tokens = tin, tout
                        call.charged_micro = math.ceil(tin * input_rate + tout * output_rate)
                    call.request_id = str(
                        getattr(response, "_request_id", None) or getattr(response, "id", None) or "-"
                    )[:200]
                    call.status = "success" if succeeded else "failed"
                    call.validation = validation
                    call.elapsed = time.monotonic() - started
                    s.flush()
                    record = {
                        key: getattr(call, key)
                        for key in (
                            "created_at",
                            "step",
                            "model",
                            "request_id",
                            "input_tokens",
                            "output_tokens",
                            "elapsed",
                            "retries",
                            "validation",
                            "status",
                        )
                    }
                if self.observer:
                    self.observer(record)
            if succeeded:
                return value
            if non_retryable:
                break
            if retry < self.settings.model_retries:
                self.sleep(min(2**retry, 4))
        messages = {
            "MODEL_AUTHENTICATION_FAILED": "模型认证失败，请核对Key的厂商、地域与接口地址",
            "MODEL_BALANCE_REQUIRED": "服务商提示余额不足，请在其控制台核对，不会自动充值",
            "MODEL_ACCESS_DENIED": "模型访问被拒绝，请核对业务空间和权限",
            "MODEL_NOT_FOUND": "服务商未找到所选模型，请核对型号与地址",
        }
        raise AppError(
            last_error or "MODEL_RESPONSE_FAILED",
            messages.get(last_error, "模型请求或输出校验失败，已用尽有限重试；请检查配置后重试"),
            502,
        )

    def embeddings(self, texts, report_id=None, query=False):
        if not texts or len(texts) > 10:
            raise AppError("INVALID_EMBEDDING_BATCH", "向量请求须包含1–10段文本")
        dimensions = self.settings.embedding_dimensions
        if self.settings.embedding_provider == "local":
            from app.services.local_embedding import LocalEmbedding

            started = time.monotonic()
            try:
                model = self.local_encoder or LocalEmbedding(self.settings)
                vectors = model.embed(texts, query=query)
                if len(vectors) != len(texts) or any(len(v) != dimensions for v in vectors):
                    raise ValueError("本机向量结构不合格")
            except Exception:
                raise AppError(
                    "LOCAL_EMBEDDING_FAILED", "本机中文向量模型加载或运行失败，请检查模型缓存与环境", 502
                ) from None
            with self.store.session(write=True) as s:
                s.add(
                    ModelCall(
                        owner_id=self.settings.owner_id,
                        month=month_now(),
                        report_id=report_id,
                        model=self.settings.embedding_model,
                        step="检索向量（本机）" if query else "资料向量（本机）",
                        charged_micro=0,
                        status="success",
                        validation="本机真实推理合规",
                        elapsed=time.monotonic() - started,
                        output_tokens=0,
                    )
                )
            return vectors

        def validate(result):
            rows = sorted(result.data, key=lambda row: row.index)
            vectors = [row.embedding for row in rows]
            if len(vectors) != len(texts) or [row.index for row in rows] != list(range(len(texts))):
                raise ValueError("向量数量错误")
            if any(len(row) != dimensions or any(not math.isfinite(x) for x in row) for row in vectors):
                raise ValueError("向量结构错误")
            return vectors

        return self.invoke(
            lambda c: c.embeddings.create(
                model=self.settings.embedding_model, input=texts, dimensions=dimensions, encoding_format="float"
            ),
            validate,
            model=self.settings.embedding_model,
            step="资料向量",
            embedding=True,
            input_bytes=sum(len(t.encode()) for t in texts),
            report_id=report_id,
        )

    def outline(self, messages, report_id):
        from app.schemas.contracts import parse_outline

        return self.invoke(
            lambda c: c.chat.completions.create(
                model=self.settings.model_name,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=self.settings.model_max_output,
                extra_body={"thinking": {"type": "disabled"}}
                if self.settings.model_provider == "deepseek"
                else {"enable_thinking": False},
            ),
            lambda result: parse_outline(result.choices[0].message.content),
            model=self.settings.model_name,
            step="生成大纲",
            report_id=report_id,
            input_bytes=sum(len(m["content"].encode()) for m in messages),
            output_cap=self.settings.model_max_output,
        )

    def json_call(self, prompt, payload, validate, *, step, report_id, model=None):
        import json

        from app.core.config import BACKEND
        from app.schemas.writing import json_value

        messages = [
            {"role": "system", "content": (BACKEND / f"app/services/prompts/{prompt}.md").read_text()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        model = model or self.settings.model_name
        return self.invoke(
            lambda c: c.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"},
                max_tokens=self.settings.model_max_output,
                extra_body={"thinking": {"type": "disabled"}}
                if self.settings.model_provider == "deepseek" else {"enable_thinking": False},
            ),
            lambda result: validate(json_value(result.choices[0].message.content)),
            model=model, step=step, report_id=report_id,
            input_bytes=sum(len(m["content"].encode()) for m in messages),
            output_cap=self.settings.model_max_output,
        )
