import importlib
import json
from pathlib import Path

import pytest


def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("evaluate"), importlib.import_module("stage_gate")


def test_original_evaluation_missing_is_closed_without_key_or_network(tmp_path, monkeypatch):
    evaluator, gate = modules(monkeypatch)
    with pytest.raises(FileNotFoundError):
        evaluator.load_suite(tmp_path / "missing.json")
    assert not gate.check(tmp_path)


def test_evaluation_references_and_code_checks(monkeypatch):
    evaluator, _ = modules(monkeypatch)
    outputs = {"draft": {"body": "正确值12000万元，[待补充：环评]"}}
    assert evaluator.references({"正文": {"$ref": "draft.body"}}, outputs)["正文"] == outputs["draft"]["body"]
    assert evaluator.check_output(
        outputs, [{"path": "draft.body", "contains": ["12000", "[待补充"], "absent": ["30000"]}]
    )
    assert not evaluator.check_output(outputs, [{"path": "draft.body", "contains": ["不存在"]}])
    with pytest.raises(ValueError):
        evaluator.check_output(outputs, [{"path": "draft.body"}])


def test_human_review_bound_to_real_run_ids_cannot_override_failure(tmp_path, monkeypatch):
    evaluator, _ = modules(monkeypatch)
    results = [
        {
            "case": f"T{i:02}",
            "trial": trial,
            "report_id": f"run-{i}-{trial}",
            "code_passed": True,
            "judge": {"passed": True, "redline": False},
            "passed": False,
        }
        for i in range(1, 14)
        for trial in range(1, 4)
    ]
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"results": results, "passed": False}))
    assert not evaluator.review_summary(tmp_path, {"T01-1": True})
    assert evaluator.review_summary(tmp_path, {r["report_id"]: True for r in results})
    results[0]["judge"]["redline"] = True
    path.write_text(json.dumps({"results": results, "passed": False}))
    assert not evaluator.review_summary(tmp_path, {r["report_id"]: True for r in results})
