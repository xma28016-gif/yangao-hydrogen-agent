"""Fail-closed, manifest-driven 13 x 3 evaluation. Never substitutes synthetic answers."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def at(value, path):
    for key in path.split(".") if path else []:
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def references(value, outputs):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return at(outputs, value["$ref"])
        return {k: references(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [references(v, outputs) for v in value]
    return value


def check_output(outputs, checks):
    passed = True
    for check in checks:
        value = at(outputs, check["path"])
        if not {"equals", "contains", "absent"} & set(check):
            raise ValueError("代码判分规则不能为空")
        if "equals" in check:
            passed &= value == check["equals"]
        if "contains" in check:
            passed &= all(t in value for t in check["contains"])
        if "absent" in check:
            passed &= all(t not in value for t in check["absent"])
    return bool(passed)


def load_suite(path):
    manifest = json.loads(path.read_text())
    if manifest.get("data_classification") != "public_or_synthetic":
        raise ValueError("只允许已授权的公开或合成评测资料")
    base = path.parent
    sources = manifest["sources"]
    if len(sources) != 11 or sum(bool(s.get("builtin")) for s in sources) != 1:
        raise ValueError("须有原11份资料，其中发改委范本内置、其余10份为资料库")
    for source in sources:
        if not (base / source["path"]).is_file():
            raise ValueError("原始资料缺失")
    if set(manifest["cases"]) != {f"T{i:02}" for i in range(1, 14)}:
        raise ValueError("必须包含T01–T13原题，不能用独立冒烟替代")
    cases = {}
    for identity, filenames in manifest["cases"].items():
        case = json.loads((base / filenames["input"]).read_text())
        answer = json.loads((base / filenames["answer"]).read_text())
        if (
            not case.get("stages")
            or not answer.get("checks")
            or not answer.get("rubric")
        ):
            raise ValueError("每题必须有原始输入、代码判分规则和原题标准")
        cases[identity] = (case, answer)
    return manifest, cases


def review_summary(output_dir, human):
    summary = json.loads((output_dir / "summary.json").read_text())
    for result in summary["results"]:
        result["human_checked"] = human.get(result["report_id"]) is True
        result["passed"] = bool(
            result.get("code_passed")
            and result.get("judge", {}).get("passed")
            and not result.get("judge", {}).get("redline")
            and result["human_checked"]
            and not result.get("error")
        )
    summary["passed"] = len(summary["results"]) == 39 and all(
        r["passed"] for r in summary["results"]
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    return summary["passed"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "data/evaluation/manifest.json"
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Preflight first; this flag enables real billed calls",
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="Apply review to existing run IDs; no model calls",
    )
    args = parser.parse_args()
    try:
        manifest, cases = load_suite(args.manifest)
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "待验：原始11份资料、truth_base标准答案及原题输入/埋错稿不齐，未发送任何模型请求。"
        )
        return 2
    if args.review_only:
        try:
            human = json.loads((args.manifest.parent / "human-review.json").read_text())
            passed = review_summary(ROOT / "data/evaluation/runs", human)
        except (OSError, ValueError, KeyError):
            passed = False
        print(
            "原题评测通过，仍须程序检查全过。"
            if passed
            else "待验或不通过，未调用模型。"
        )
        return 0 if passed else 2
    if not args.run:
        print("评测清单预检通过。尚未运行39次独立模型评测，未判通过。")
        return 2
    from app.core.config import BACKEND, Settings
    from app.models.tables import Report
    from app.repositories.store import Store
    from app.schemas.contracts import ModelOutline
    from app.schemas.writing import (
        DraftOutput,
        normalize_facts,
        validate_audit,
        validate_citations,
        validate_rewrite,
    )
    from app.services.model_gateway import Gateway
    from pydantic import BaseModel, Field

    class Judge(BaseModel):
        passed: bool
        redline: bool
        reasons: list[str] = Field(min_length=1)

    settings = Settings()
    if settings.model_provider != "deepseek":
        print("待验：独立评审模型须先按当前厂商核实，未调用模型。")
        return 2
    store = Store(settings)
    gateway = Gateway(store)
    output_dir = settings.data_dir / "evaluation/runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        Path(source["path"]).name
        for source in manifest["sources"]
        if not source.get("builtin")
    }
    review_path = args.manifest.parent / "human-review.json"
    human = json.loads(review_path.read_text()) if review_path.exists() else {}
    results = []
    for identity, (case, answer) in cases.items():
        for trial in range(1, 4):
            with store.session(write=True) as s:
                report = Report(
                    owner_id=settings.owner_id,
                    title=f"原题评测 {identity} 第{trial}次",
                    target_words=12000,
                )
                s.add(report)
                s.flush()
                report_id = report.id
            outputs, record = (
                {},
                {
                    "case": identity,
                    "trial": trial,
                    "report_id": report_id,
                    "passed": False,
                },
            )
            try:
                for stage in case["stages"]:
                    payload = references(stage["payload"], outputs)
                    prompt = stage["prompt"]
                    if prompt not in {"outline", "facts", "write", "audit"}:
                        raise ValueError("不支持的评测环节")

                    def validate(value, prompt=prompt, payload=payload):
                        if prompt == "outline":
                            return ModelOutline.model_validate(value).model_dump()
                        if prompt == "facts":
                            return normalize_facts(value, sources)
                        if prompt == "audit":
                            return validate_audit(
                                value, payload["正文"], payload["固定事实"]
                            )
                        draft = DraftOutput.model_validate(value)
                        validate_citations(draft.body, sources)
                        if payload.get("模式") == "重写":
                            validate_rewrite(
                                payload["原稿"], draft.body, payload["审计"]["issues"]
                            )
                        return draft.model_dump()

                    if prompt == "outline":
                        payload["内置范本全文"] = (
                            BACKEND / "app/assets/ndrc-enterprise-2023.txt"
                        ).read_text()
                    outputs[stage["id"]] = gateway.json_call(
                        prompt,
                        payload,
                        validate,
                        step=f"评测{identity}-{stage['id']}",
                        report_id=report_id,
                    )
                code_passed = check_output(outputs, answer["checks"])
                judged = gateway.json_call(
                    "judge",
                    {
                        "原题标准": answer["rubric"],
                        "原题输入": case,
                        "标准答案及代码规则": answer,
                        "实际输出": outputs,
                    },
                    Judge.model_validate,
                    step=f"评审{identity}",
                    report_id=report_id,
                    model="deepseek-v4-pro",
                )
                key = report_id
                record.update(
                    code_passed=code_passed,
                    judge=judged.model_dump(),
                    human_checked=human.get(key) is True,
                    passed=code_passed
                    and judged.passed
                    and not judged.redline
                    and human.get(key) is True,
                    outputs=outputs,
                )
            except Exception:
                record["error"] = "调用、结构或判分未通过；请在本机调用记录核对脱敏状态"
            (output_dir / f"{report_id}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2)
            )
            results.append({k: v for k, v in record.items() if k != "outputs"})
            print(
                f"{identity} 第{trial}次："
                + ("通过" if record["passed"] else "未通过或待人工抽检"),
                flush=True,
            )
    passed = len(results) == 39 and all(r["passed"] for r in results)
    summary = {
        "passed": passed,
        "total": len(results),
        "results": results,
        "rule": "13题各独立3次全过且核心错误为0；仍须C01–C10全部通过",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(
        "原题模型评测通过；仍须程序检查全部通过。"
        if passed
        else "待验或不通过，不可交付研究员试用。"
    )
    store.engine.dispose()
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
