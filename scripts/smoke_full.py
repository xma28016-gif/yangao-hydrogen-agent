"""Independent synthetic full-flow smoke using the real running API and worker."""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    import httpx
    from app.core.config import Settings
    from app.models.tables import ModelCall
    from app.repositories.store import Store
    from app.schemas.writing import validate_audit
    from app.services.model_gateway import Gateway

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--resume", help="Continue a previously created synthetic smoke report"
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.model_api_key.get_secret_value().strip():
        print("待验：未配置Key；未创建冒烟记录。")
        return 2
    store, started = Store(settings), time.time()
    client = httpx.Client(base_url="http://127.0.0.1:18080/api/v1", timeout=30)
    report_id = args.resume
    result = 1

    def checked(response):
        if response.status_code >= 400:
            raise RuntimeError(
                response.json().get("error", {}).get("code", "HTTP_ERROR")
            )
        return response.json()

    def wait(identity):
        deadline, previous = time.monotonic() + 2400, None
        while time.monotonic() < deadline:
            task = checked(client.get("/tasks/" + identity))
            if task["progress"] != previous:
                print(task["progress"], flush=True)
                previous = task["progress"]
            if task["status"] == "failed":
                raise RuntimeError(task["error_code"])
            if task["status"] == "succeeded":
                return
            time.sleep(2)
        raise RuntimeError("SMOKE_TIMEOUT")

    try:
        if not report_id:
            text = """独立合成冒烟资料的正式补充参数，仅用于同一个示范项目的功能验收，不是原始13题评测资料。
本示范项目总投资12000万元，其中建设投资11000万元、建设期利息300万元、流动资金700万元。资本金比例30%，金额3600万元，贷款8400万元。
项目采用PEM电解水制氢，以外购绿电为电源。配置2台1000Nm³/h电解槽，年产氢量1200吨。直流电耗4.8kWh/Nm³。
一期加注压力35MPa，站内储氢压力45MPa；不建设70MPa加注系统。
项目含A制氢加氢站、B加氢站。A站加注能力1000kg/d，B站500kg/d，通过长管拖车运氢。A站占地12000平方米，B站5000平方米。
建设期12个月，2027年1月开工，2027年12月底投运。制氢成本22元/kg，对应电价0.35元/kWh。项目内部收益率6.5%，投资回收期12年。
环评、安评结论、排放数据、B站运距、拖车数量与年耗电量未提供，必须待补充，不推算。"""
            upload = checked(
                client.post(
                    "/documents",
                    files={
                        "files": (
                            "独立合成冒烟正式参数.txt",
                            text.encode(),
                            "text/plain",
                        )
                    },
                )
            )
            wait(upload["documents"][0]["task_id"])
            report = checked(
                client.post(
                    "/reports",
                    json={
                        "title": "独立合成全文冒烟：氢能项目可研报告",
                        "target_words": 12000,
                    },
                )
            )
            report_id = report["id"]
            (settings.data_dir / "last-full-smoke-report.txt").write_text(report_id)
            wait(checked(client.post(f"/reports/{report_id}/outline/generate"))["id"])
            report = checked(client.get(f"/reports/{report_id}"))
            checked(
                client.post(
                    f"/reports/{report_id}/outline/confirm",
                    json={"version": report["version"]},
                )
            )
        else:
            report = checked(client.get(f"/reports/{report_id}"))
            if not report["title"].startswith("独立合成全文冒烟"):
                raise RuntimeError("RESUME_ONLY_SYNTHETIC_SMOKE")
        print("报告ID：" + report_id, flush=True)
        wait(
            checked(
                client.post(
                    f"/reports/{report_id}/write", json={"version": report["version"]}
                )
            )["id"]
        )
        content = checked(client.get(f"/reports/{report_id}/content"))
        assert content["status"] == "completed" and 8 <= len(content["chapters"]) <= 13
        assert content["words"] < 20000
        assert all(
            c["body"] and c["words"] <= c["max_words"] for c in content["chapters"]
        )
        assert "truth_base" not in str(content)
        if args.resume:
            # Recheck a completed report with a NEW real audit, without claiming regeneration.
            from app.models.tables import Report

            with store.session() as s:
                facts = store.get(s, Report, report_id).truth_base
            first_body = content["chapters"][0]["body"]
            Gateway(store).json_call(
                "audit",
                {"正文": first_body, "固定事实": facts},
                lambda value: validate_audit(value, first_body, facts),
                step="冒烟复核定稿",
                report_id=report_id,
            )
            print("已完成稿新增真实审计复核；未重新生成全文。", flush=True)
        # Save an actual human-edit simulation through the public API; preserve original_body.
        first = content["chapters"][0]
        marker = "\n本稿为独立合成资料的验收草稿。"
        if not first["edited"] and first["words"] + len(marker) < first["max_words"]:
            changed = checked(
                client.put(
                    f"/reports/{report_id}/chapters/1",
                    json={
                        "body": first["body"] + marker,
                        "version": first["version"],
                    },
                )
            )
            assert changed["edited"]
        docx = client.get(f"/reports/{report_id}/export")
        assert docx.status_code == 200 and docx.content.startswith(b"PK")
        path = settings.data_dir / "qa" / "full-smoke.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(docx.content)
        result = 0
        print(
            f"真实全文、编辑及Word导出链路通过：{len(content['chapters'])}章，{content['words']}字。",
            flush=True,
        )
        print("此结果不代表原13题评测通过。", flush=True)
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, RuntimeError)
            else "SMOKE_ASSERTION_OR_REQUEST_FAILED"
        )
        print("冒烟未通过：" + code, flush=True)
    finally:
        with store.session() as s:
            calls = [c for c in store.list(s, ModelCall) if c.created_at >= started]
        evidence = ROOT / "docs/evidence/阶段2-2/冒烟记录.md"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        if calls:
            if not evidence.exists():
                evidence.write_text(
                    "# 冒烟记录（脚本自动生成，不得手填）\n- 产品：研稿\n- 阶段：阶段2-2\n"
                    f"- 模型：{settings.model_name}；{settings.embedding_model}\n"
                )
            tz = ZoneInfo("Asia/Shanghai")
            with evidence.open("a") as f:
                f.write(
                    f"\n## 运行 {datetime.now(tz):%Y-%m-%d %H:%M:%S}\n- 报告ID：{report_id}\n"
                )
                f.write(
                    "\n| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 首字耗时 | 总耗时 | 结构校验 | 重试次数 | 结果 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                )
                for c in sorted(calls, key=lambda c: c.created_at):
                    when = datetime.fromtimestamp(c.created_at, tz).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    rid = c.request_id.replace("|", "").replace("\n", "")[:200]
                    outcome = (
                        "失败"
                        if c.status != "success"
                        else ("通过" if result == 0 else "瑕疵待打分")
                    )
                    f.write(
                        f"| {when} | {c.step} / {c.model} | {rid} | {c.input_tokens if c.input_tokens is not None else '-'} | {c.output_tokens if c.output_tokens is not None else '-'} | N/A（非流式） | {c.elapsed:.2f}s | {c.validation} | {c.retries} | {outcome} |\n"
                    )
                f.write("\n- 本轮结果：" + ("通过" if result == 0 else "失败") + "\n")
        else:
            print("本轮未发生新模型调用，未新增真实模型证据。", flush=True)
        client.close()
        store.engine.dispose()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
