"""Real upstream smoke only. No fake transport or manual evidence path."""

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from app.core.config import Settings
    from app.models.tables import ModelCall
    from app.repositories.store import Store

    settings = Settings()
    if not settings.model_api_key.get_secret_value().strip():
        print("待验：未配置模型 Key。未调用模型，未创建冒烟记录。")
        return 2
    import re

    import httpx

    store = Store(settings)
    started = time.time()
    evidence = ROOT / "docs/evidence/阶段2-1/冒烟记录.md"
    # Existing production user data is preserved; smoke creates visibly labelled new records.
    client = httpx.Client(base_url="http://127.0.0.1:18080/api/v1", timeout=10)

    def checked(response):
        if response.status_code >= 400:
            raise RuntimeError(response.json().get("error", {}).get("code", "HTTP_ERROR"))
        return response.json()

    def wait(identity):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            task = checked(client.get("/tasks/" + identity))
            if task["status"] == "failed":
                raise RuntimeError(task["error_code"])
            if task["status"] == "succeeded":
                return
            time.sleep(1)
        raise RuntimeError("SMOKE_TIMEOUT")

    result = 0
    try:
        if not checked(client.get("/health"))["model_configured"]:
            raise RuntimeError("SERVER_NEEDS_RESTART_AFTER_KEY_CONFIGURATION")
        text = "独立合成冒烟资料，不是原始13题评测集。此示范项目拟开展绿色制氢可行性研究，建设必要性包括清洁能源消纳和产业需求；方案需分析选址、建设、运营、投融资、环境影响及风险。投资和设备规模尚未明确，应待补充。"
        uploaded = checked(
            client.post("/documents", files={"files": ("独立合成冒烟资料.txt", text.encode(), "text/plain")})
        )
        wait(uploaded["documents"][0]["task_id"])
        hits = checked(client.post("/search", json={"query": "绿色制氢建设必要性", "limit": 3}))["hits"]
        if not hits:
            raise RuntimeError("RETRIEVAL_EMPTY")
        report = checked(
            client.post("/reports", json={"title": "独立合成冒烟：氢能项目可研报告", "target_words": 12000})
        )
        task = checked(client.post("/reports/" + report["id"] + "/outline/generate"))
        wait(task["id"])
        report = checked(client.get("/reports/" + report["id"]))
        if report["notices"] and any("数字" in notice or "章数" in notice for notice in report["notices"]):
            raise RuntimeError("OUTLINE_QUALITY_REQUIRES_REVIEW")
        checked(client.post("/reports/" + report["id"] + "/outline/confirm", json={"version": report["version"]}))
        print("真实向量、检索、大纲及持久确认链路通过；报告 ID：" + report["id"])
        print("请在临时验收页打开该报告，人工评估十个主题覆盖和论点质量。本结果不代表原13题评测通过。")
    except Exception:
        print("真实冒烟未通过；请在本机任务状态与调用记录中查看脱敏错误码，未输出上游异常或内容。")
        result = 1
    finally:
        with store.session() as s:
            calls = [row for row in store.list(s, ModelCall) if row.created_at >= started]
        header = f"{settings.model_provider} / {settings.model_name}；{settings.embedding_provider} / {settings.embedding_model}"
        if not evidence.exists():
            evidence.write_text(
                "# 冒烟记录（冒烟脚本自动写入，不得手填）\n- 产品：研稿\n- 阶段：阶段2-1\n- 模型：" + header + "\n"
            )
        else:
            history = evidence.read_text()
            previous = re.search(r"^- 模型：(.*)$", history, flags=re.MULTILINE)
            if previous and previous.group(1) != header:
                # Update generated metadata while preserving earlier provider history and every call row.
                if "- 初始历史模型：" not in history:
                    history = history.replace(
                        previous.group(0), previous.group(0) + "\n- 初始历史模型：" + previous.group(1), 1
                    )
                history = re.sub(r"^- 模型：.*$", "- 模型：" + header, history, count=1, flags=re.MULTILINE)
                evidence.write_text(history)
        now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        with evidence.open("a") as f:
            f.write(
                f"\n## 运行 {now}\n- 本轮模型：{settings.model_provider} / {settings.model_name}；{settings.embedding_provider} / {settings.embedding_model}\n"
            )
            f.write(
                "\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 首字耗时 | 总耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            for row in sorted(calls, key=lambda c: c.created_at):
                when = datetime.fromtimestamp(row.created_at, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
                outcome = "失败" if row.status != "success" else ("瑕疵待打分" if result else "通过")
                rid = row.request_id.replace("|", "").replace("\n", "")[:200]
                chain = (row.step + " / " + row.model).replace("|", "").replace("\n", "")
                f.write(
                    f"| {when} | {chain} | {rid} | {row.input_tokens if row.input_tokens is not None else '-'} | {row.output_tokens if row.output_tokens is not None else '-'} | N/A（非流式） | {row.elapsed:.2f}s | {row.validation} | {row.retries} | {outcome} |\n"
                )
            f.write("\n- 本轮结果：" + ("通过" if result == 0 else "失败") + "\n")
        client.close()
        store.engine.dispose()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
