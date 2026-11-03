import io
import json
from types import SimpleNamespace as NS

import pytest
from app.core.config import AppError
from app.models.tables import Event, Report
from app.schemas.writing import (
    FIELDS,
    chapter_caps,
    char_count,
    ending,
    normalize_facts,
    truncate_body,
    validate_audit,
    validate_citations,
    validate_rewrite,
)
from app.services.exporting import modification_ratio
from app.services.writing import chapters
from conftest import outline, ready_document, run_pending
from docx import Document
from docx.enum.text import WD_COLOR_INDEX


def writing_env(env, *, mode="good", target=800):
    docid = ready_document(env)
    report = env.api.post("/api/v1/reports", json={"title": "合成验证报告", "target_words": target}).json()
    rows = outline(8, target // 8)["full_outline"]
    rows[-1]["suggested_words"] += target - sum(c["suggested_words"] for c in rows)
    report = env.api.put(
        f"/api/v1/reports/{report['id']}/outline", json={"chapters": rows, "version": report["version"]}
    ).json()
    report = env.api.post(f"/api/v1/reports/{report['id']}/outline/confirm", json={"version": report["version"]}).json()
    calls, searches = [], []
    state = {"mode": mode, "failed": False}

    def search(query, limit=6, **kwargs):
        searches.append((query, limit, kwargs))
        return [{"filename": "独立测试资料.txt", "text": "总投资12000万元。", "chunk_id": "test", "score": 1}]

    env.pipeline.retrieval.search = search

    def call(prompt, payload, validate, *, step, report_id, **kwargs):
        calls.append((prompt, payload, step))
        if prompt == "facts":
            value = {
                "validated_data": {"total_investment": "12000万元", "extra": "丢弃"},
                "sources": {"total_investment": "独立测试资料.txt"},
                "global_terminology": ["PEM"],
            }
        elif prompt == "write":
            number = payload["章节"]["chapter_id"]
            body = "总投资12000万元。缺少[待补充：环评]。末句用于承接。"
            if state["mode"] in {"rewrite", "critical", "pause"} and payload.get("模式") != "重写":
                body = "总投资30000万元。缺少[待补充：环评]。末句用于承接。"
            if state["mode"] == "critical":
                body = "总投资30000万元。缺少[待补充：环评]。末句用于承接。"
            if state["mode"] == "long":
                body = "投资12000万元。" + "这是超过本章上限的铺陈。" * 1000
            value = {
                "body": body,
                "summary": "不要把这个虚构摘要当作末句。",
                "missing": [],
                "user_notice": "与项目资料不一致" if number < 3 else "",
            }
        else:
            if state["mode"] == "pause" and not state["failed"] and len([c for c in calls if c[2] == "写章"]) == 2:
                state["failed"] = True
                raise AppError("MODEL_RESPONSE_FAILED", "离线故障注入", 502)
            issues = []
            if "30000" in payload["正文"]:
                issues = [
                    {
                        "severity": "CRITICAL",
                        "quote": "总投资30000万元。",
                        "expected": "总投资12000万元。",
                        "basis": "total_investment",
                    }
                ]
            elif state["mode"] == "warning":
                issues = [
                    {
                        "severity": "WARNING",
                        "quote": "末句用于承接。",
                        "expected": "术语提示",
                        "basis": "global_terminology",
                    }
                ]
            value = {"issues": issues, "unverifiable": ["未提供的设备分项价"]}
        return validate(value)

    env.gateway.json_call = call

    def start():
        response = env.api.post(f"/api/v1/reports/{report['id']}/write", json={"version": report["version"]})
        assert response.status_code == 202, response.text
        return response.json()

    return NS(
        report=report,
        docid=docid,
        calls=calls,
        searches=searches,
        state=state,
        start=start,
        content=lambda: env.api.get(f"/api/v1/reports/{report['id']}/content").json(),
    )


def test_C01_C09_retrieval_snapshot_truth_once_and_private(setup):
    e = writing_env(setup)
    first = e.start()
    assert e.start()["id"] == first["id"]
    ready_document(setup)  # Later upload cannot join an already frozen report corpus.
    run_pending(setup)
    data = e.content()
    assert data["status"] == "completed"
    assert [limit for _, limit, _ in e.searches] == [3] * 11 + [6] * 8
    assert all(options["document_ids"] == [e.docid] for _, _, options in e.searches)
    assert len([c for c in e.calls if c[0] == "facts"]) == 1
    with setup.store.session() as s:
        facts = setup.store.get(s, Report, e.report["id"]).truth_base
        assert set(facts["validated_data"]) == set(FIELDS)
        assert facts["validated_data"]["financial"] is None
    assert "truth_base" not in json.dumps(data)
    assert "total_investment" not in json.dumps(data)
    assert e.start()["id"] == first["id"]
    assert len([c for c in e.calls if c[0] == "facts"]) == 1


def test_C04_warning_never_rewrites(setup):
    e = writing_env(setup, mode="warning")
    e.start()
    run_pending(setup)
    assert all(c["rewrite_count"] == 0 and c["status"] == "ready" for c in e.content()["chapters"])
    assert not any(step == "重写" for _, _, step in e.calls)


def test_C05_critical_two_rewrites_then_manual(setup):
    e = writing_env(setup, mode="critical")
    e.start()
    run_pending(setup)
    assert all(c["rewrite_count"] == 2 and c["status"] == "needs_manual" for c in e.content()["chapters"])
    assert all(c["audit"]["issues"][0]["quote"] in c["body"] for c in e.content()["chapters"])


def test_C03_C06_C07_resume_keeps_final_memory_and_current_checkpoint(setup):
    e = writing_env(setup, mode="pause")
    t = e.start()
    run_pending(setup)
    assert e.content()["status"] == "paused"
    assert e.content()["chapters"][0]["status"] == "ready"
    assert e.content()["chapters"][1]["status"] == "auditing"
    assert e.start()["id"] == t["id"]
    run_pending(setup)
    assert e.content()["status"] == "completed"
    assert len([c for c in e.calls if c[0] == "facts"]) == 1
    assert len([c for c in e.calls if c[2] == "写章"]) == 8
    second = next(p for _, p, step in e.calls if step == "写章" and p["章节"]["chapter_id"] == 2)
    assert "30000" not in second["已定稿摘要"][0]
    assert second["上一章正文末句"] == "末句用于承接。"
    with setup.store.session() as s:
        saved = chapters(setup.store, s, e.report["id"])
        assert saved[0].last_ending == ending(saved[0].body)
        assert saved[0].summary == saved[0].body[:100]


def test_C08_notice_deduplication(setup):
    e = writing_env(setup)
    e.start()
    run_pending(setup)
    assert e.content()["notices"] == ["与项目资料不一致"]


def test_C10_compress_once_truncate_and_title_budget(setup):
    e = writing_env(setup, mode="long", target=18000)
    e.start()
    run_pending(setup)
    data = e.content()
    assert data["status"] == "completed"
    assert data["words"] < 20000
    assert all(c["words"] <= c["max_words"] and c["truncated"] for c in data["chapters"])
    assert len([c for c in e.calls if c[2] == "压缩"]) == 8
    assert all(c["body"].endswith("。") for c in data["chapters"])
    assert sum(chapter_caps("题" * 200, [{"title": "长" * 200, "suggested_words": 1800}] * 10)) + 2200 < 20000
    assert setup.api.post("/api/v1/reports", json={"title": "超额", "target_words": 18001}).status_code == 422


def test_edit_lock_original_export_highlight_and_modification_event(setup):
    e = writing_env(setup)
    e.start()
    run_pending(setup)
    ch = e.content()["chapters"][0]
    uri = f"/api/v1/reports/{e.report['id']}/chapters/1"
    revised = ch["body"] + "人工核对。"
    response = setup.api.put(uri, json={"body": revised, "version": ch["version"]})
    assert response.status_code == 200, response.text
    assert setup.api.put(uri, json={"body": revised, "version": ch["version"]}).status_code == 409
    assert (
        setup.api.put(uri, json={"body": "字" * (ch["max_words"] + 1), "version": ch["version"] + 1}).status_code == 409
    )
    response = setup.api.get(f"/api/v1/reports/{e.report['id']}/export")
    assert response.status_code == 200
    doc = Document(io.BytesIO(response.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert revised in text and "truth_base" not in text
    assert any(r.font.highlight_color == WD_COLOR_INDEX.YELLOW for p in doc.paragraphs for r in p.runs)
    with setup.store.session() as s:
        original = chapters(setup.store, s, e.report["id"])[0].original_body
        events = [row for row in setup.store.list(s, Event) if row.name == "E3"]
        assert original == ch["body"] and events[0].details["modification_ratio"] > 0
    assert setup.api.post(f"/api/v1/reports/{e.report['id']}/outline/generate").status_code == 409
    assert modification_ratio("相同", "相同") == 0
    assert modification_ratio("甲", "乙") == 1


def test_write_requires_confirm_and_owner_guard(setup):
    e = writing_env(setup)
    with setup.store.session(write=True) as s:
        row = setup.store.get(s, Report, e.report["id"])
        row.confirmed_at = None
    assert (
        setup.api.post(f"/api/v1/reports/{e.report['id']}/write", json={"version": e.report["version"]}).status_code
        == 409
    )
    with setup.store.session(write=True) as s:
        row = setup.store.get(s, Report, e.report["id"])
        row.owner_id = "another-owner"
    for path in ["/content", "/export"]:
        assert setup.api.get(f"/api/v1/reports/{e.report['id']}" + path).status_code == 404
    assert (
        setup.api.put(f"/api/v1/reports/{e.report['id']}/chapters/1", json={"body": "正文", "version": 1}).status_code
        == 404
    )


def test_output_guards_rewrite_audit_sources_and_normalization():
    facts = normalize_facts(
        {"validated_data": {"financial": "伪值", "technology_route": 3}, "sources": {"financial": "未知.txt"}},
        {"资料.txt"},
    )
    assert all(value is None for value in facts["validated_data"].values())
    with pytest.raises(ValueError):
        validate_audit(
            {"issues": [{"severity": "CRITICAL", "quote": "原句", "expected": "正确", "basis": "financial"}]},
            "原句",
            facts,
        )
    with pytest.raises(ValueError):
        validate_citations("来源[伪造.pdf]", {"资料.txt"})
    with pytest.raises(ValueError):
        validate_rewrite(
            "错句。固定内容[待补充：数据]。", "正确。固定内容12。", [{"severity": "CRITICAL", "quote": "错句。"}]
        )
    validate_rewrite(
        "错句。固定内容[待补充：数据]。",
        "正确。固定内容[待补充：数据]。",
        [{"severity": "CRITICAL", "quote": "错句。"}],
    )
    assert char_count(truncate_body("一句。第二句还未完", 6)) <= 6


def test_completed_chapters_and_facts_survive_new_store(setup):
    from app.repositories.store import Store

    e = writing_env(setup)
    e.start()
    run_pending(setup)
    second = Store(setup.settings)
    with second.session() as s:
        report = second.get(s, Report, e.report["id"])
        assert report.status == "completed" and report.truth_base["validated_data"]["total_investment"] == "12000万元"
        assert len(chapters(second, s, report.id)) == 8
        assert all(ch.phase == "done" for ch in chapters(second, s, report.id))
    second.engine.dispose()
