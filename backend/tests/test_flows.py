import io
import json
import time
import zipfile

import pytest
from app.core.config import AppError
from app.models.tables import Document, ModelCall, Report, Task
from app.repositories.store import Store
from app.schemas.contracts import ModelOutline, normalize, parse_outline
from app.services.model_gateway import Gateway
from app.services.parse_file import parse, validate_container
from conftest import make_report, outline, ready_document, run_pending
from docx import Document as Word
from pydantic import SecretStr
from pypdf import PdfWriter


def test_real_upload_index_search_and_persistence(setup):
    env = setup
    response = env.api.post(
        "/api/v1/documents", files=[("files", ("项目.md", ("建设目标为绿色制氢。" * 200).encode(), "text/markdown"))]
    )
    assert response.status_code == 202
    run_pending(env)
    document = env.api.get("/api/v1/documents").json()["documents"][0]
    assert document["status"] == "ready" and document["chunks_count"] > 1
    hits = env.api.post("/api/v1/search", json={"query": "绿色制氢", "limit": 3}).json()["hits"]
    assert hits and hits[0]["filename"] == "项目.md" and "绿色制氢" in hits[0]["text"]
    other = Store(env.settings)
    with other.session() as s:
        assert other.get(s, Document, document["id"]).status == "ready"
    other.engine.dispose()


def test_outline_to_manual_confirmation_and_idempotent_submit(setup):
    env = setup
    report = make_report(env)
    first = env.api.post(f"/api/v1/reports/{report['id']}/outline/generate")
    second = env.api.post(f"/api/v1/reports/{report['id']}/outline/generate")
    assert first.status_code == 202 and first.json()["id"] == second.json()["id"]
    run_pending(env)
    report = env.api.get("/api/v1/reports/" + report["id"]).json()
    assert len(report["outline"]) == 10
    assert sum(c["suggested_words"] for c in report["outline"]) == 12000
    report["outline"][0]["title"] = "修改后的概述"
    saved = env.api.put(
        f"/api/v1/reports/{report['id']}/outline", json={"chapters": report["outline"], "version": report["version"]}
    )
    assert saved.status_code == 200
    version = saved.json()["version"]
    confirmed = env.api.post(f"/api/v1/reports/{report['id']}/outline/confirm", json={"version": version})
    assert confirmed.status_code == 200 and confirmed.json()["confirmed_at"]
    assert env.api.post(f"/api/v1/reports/{report['id']}/outline/generate").status_code == 409
    assert env.api.post(f"/api/v1/reports/{report['id']}/outline/confirm", json={"version": version}).status_code == 200


def test_outline_prompt_does_not_retrieve_user_material(setup):
    report = make_report(setup)
    setup.api.post(f"/api/v1/reports/{report['id']}/outline/generate")
    run_pending(setup)
    calls = [call for kind, call in setup.fake.calls if kind == "chat"]
    assert len(calls) == 1
    assert "内置范本全文" in calls[0]["messages"][1]["content"]
    assert "企业投资项目可行性研究报告编写参考大纲" in calls[0]["messages"][1]["content"]
    assert not any(kind == "embedding" for kind, _ in setup.fake.calls)


def test_invalid_chapter_count_regenerates_only_once(setup):
    report = make_report(setup)
    setup.fake.count = 14
    setup.api.post(f"/api/v1/reports/{report['id']}/outline/generate")
    run_pending(setup)
    report = setup.api.get("/api/v1/reports/" + report["id"]).json()
    assert len(report["outline"]) == 14 and report["notices"]
    assert len([1 for kind, _ in setup.fake.calls if kind == "chat"]) == 2
    assert (
        setup.api.post(
            f"/api/v1/reports/{report['id']}/outline/confirm", json={"version": report["version"]}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "filename,content",
    [
        ("fake.pdf", b"not pdf"),
        ("../data.txt", b"test"),
        ("fake.docx", b"not zip"),
        ("data.exe", b"MZ"),
        ("x.txt", b"\x00binary"),
        ("empty.txt", b""),
    ],
)
def test_unsafe_or_forged_upload_rejected(setup, filename, content):
    response = setup.api.post("/api/v1/documents", files={"files": (filename, content)})
    assert response.status_code == 400 and set(response.json()) == {"error"}
    assert setup.api.get("/api/v1/documents").json()["documents"] == []
    assert list((setup.settings.data_dir / "files").iterdir()) == []


def test_upload_size_and_batch_are_atomic(setup):
    setup.settings.max_file_bytes = 10
    response = setup.api.post(
        "/api/v1/documents", files=[("files", ("good.txt", b"hello")), ("files", ("large.txt", b"x" * 11))]
    )
    assert response.status_code == 413
    assert setup.api.get("/api/v1/documents").json()["documents"] == []
    assert not list((setup.settings.data_dir / "files").iterdir())


def test_scanned_pdf_no_text_not_success(setup):
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=100)
    data = io.BytesIO()
    pdf.write(data)
    setup.api.post("/api/v1/documents", files={"files": ("scan.pdf", data.getvalue(), "application/pdf")})
    run_pending(setup)
    row = setup.api.get("/api/v1/documents").json()["documents"][0]
    assert row["status"] == "no_text" and row["chunks_count"] == 0
    assert setup.fake.calls == []


def test_docx_actual_parse_includes_table(tmp_path):
    doc = Word()
    doc.add_paragraph("合成项目")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "资料表格"
    path = tmp_path / "sample.docx"
    doc.save(path)
    result = parse(path, ".docx")
    assert "资料表格" in result["text"] and not result["no_text"]


def test_docx_path_traversal_rejected(tmp_path):
    path = tmp_path / "bad.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<doc/>")
        z.writestr("../outside.txt", "bad")
    with pytest.raises(ValueError):
        validate_container(path, ".docx")


def test_create_requires_available_documents_and_target_limit(setup):
    assert setup.api.post("/api/v1/reports", json={"title": "test"}).status_code == 409
    ready_document(setup)
    for value in (18001, 0, "12000", True):
        response = setup.api.post("/api/v1/reports", json={"title": "test", "target_words": value})
        assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_edit_is_not_silently_rebalanced_and_optimistic_lock(setup):
    report = make_report(setup)
    chapters = normalize(ModelOutline.model_validate(outline()), report["target_words"])[0]
    chapters[0]["suggested_words"] += 1
    path = f"/api/v1/reports/{report['id']}/outline"
    assert setup.api.put(path, json={"chapters": chapters, "version": 1}).status_code == 400
    chapters[0]["suggested_words"] -= 1
    assert setup.api.put(path, json={"chapters": chapters, "version": 1}).status_code == 200
    assert setup.api.put(path, json={"chapters": chapters, "version": 1}).status_code == 409


def test_request_boundary_and_errors_hide_internal_details(setup):
    for response in [
        setup.api.get("/api/v1/not-found"),
        setup.api.post("/api/v1/reports", json={"title": ""}),
        setup.api.post("/api/v1/reports", json={"title": "x"}, headers={"Origin": "https://external.test"}),
    ]:
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message"}
        assert "Traceback" not in response.text


def test_owner_isolation_on_read_and_write(setup):
    report = make_report(setup)
    with setup.store.session(write=True) as s:
        row = setup.store.get(s, Report, report["id"])
        row.owner_id = "another-person"
    assert setup.api.get("/api/v1/reports").json()["reports"] == []
    assert setup.api.get("/api/v1/reports/" + report["id"]).status_code == 404
    assert setup.api.post(f"/api/v1/reports/{report['id']}/outline/generate").status_code == 404


def test_expired_task_can_be_claimed_but_live_task_cannot(setup):
    report = make_report(setup)
    setup.api.post(f"/api/v1/reports/{report['id']}/outline/generate")
    task = setup.pipeline.claim("worker-before-crash")
    assert setup.pipeline.claim("worker-other") is None
    with setup.store.session(write=True) as s:
        row = setup.store.get(s, Task, task.id)
        row.lease_until = time.time() - 1
    reclaimed = setup.pipeline.claim("worker-after-crash")
    assert reclaimed.id == task.id and reclaimed.attempts == 2
    with setup.store.session(write=True) as s:
        with pytest.raises(AppError):
            setup.pipeline.check_lease(s, task.id, "worker-before-crash")
    setup.pipeline.run(reclaimed, "worker-after-crash")
    assert setup.api.get("/api/v1/tasks/" + task.id).json()["status"] == "succeeded"


def test_embedding_resume_does_not_rebill_saved_vectors(setup):
    response = setup.api.post("/api/v1/documents", files={"files": ("resume.txt", "合成公开样例".encode())})
    identity = response.json()["documents"][0]["document"]["id"]
    run_pending(setup)
    calls = len(setup.fake.calls)
    with setup.store.session(write=True) as s:
        doc = setup.store.get(s, Document, identity)
        doc.status = "failed"
    assert setup.api.post(f"/api/v1/documents/{identity}/retry").status_code == 202
    run_pending(setup)
    assert len(setup.fake.calls) == calls
    assert setup.api.get("/api/v1/documents").json()["documents"][0]["status"] == "ready"


def test_budget_prevents_network_request_and_persists(setup):
    setup.settings.monthly_budget_yuan = 0.000001
    with pytest.raises(AppError, match="BUDGET_EXCEEDED"):
        setup.gateway.embeddings(["test"])
    assert setup.fake.calls == []


def test_missing_key_no_call_or_smoke_evidence(setup):
    setup.settings.model_api_key = SecretStr("")
    with pytest.raises(AppError, match="MODEL_NOT_CONFIGURED"):
        setup.gateway.embeddings(["test"])
    assert setup.fake.calls == []
    with setup.store.session() as s:
        assert setup.store.list(s, ModelCall) == []


def test_initialization_failure_is_bounded_and_not_billed(setup):
    attempts = []

    def fail():
        attempts.append(1)
        raise RuntimeError("do-not-return-this-secret")

    gateway = Gateway(setup.store, client_factory=fail, sleep=lambda _: None)
    with pytest.raises(AppError, match="MODEL_INITIALIZATION_FAILED"):
        gateway.embeddings(["test"])
    assert len(attempts) == 4
    assert gateway.budget()["used_yuan"] == 0


def test_failed_network_keeps_conservative_reservations(setup):
    calls = []

    def fail(client):
        calls.append(1)
        raise TimeoutError("sensitive-upstream-text")

    with pytest.raises(AppError, match="MODEL_RESPONSE_FAILED"):
        setup.gateway.invoke(
            fail, lambda _: None, model=setup.settings.model_name, step="test", input_bytes=10, output_cap=10
        )
    assert len(calls) == 4
    assert setup.gateway.budget()["used_yuan"] > 0
    with setup.store.session() as s:
        assert len(setup.store.list(s, ModelCall)) == 4


def test_bad_json_is_retried_but_usage_is_charged(setup):
    original = setup.fake.chat_create
    attempts = []

    def bad(**kwargs):
        response = original(**kwargs)
        attempts.append(1)
        response.choices[0].message.content = "not json"
        return response

    setup.fake.chat.completions.create = bad
    with pytest.raises(AppError):
        setup.gateway.outline([{"role": "user", "content": "JSON test"}], None)
    assert len(attempts) == 4
    with setup.store.session() as s:
        rows = setup.store.list(s, ModelCall)
        assert all(row.input_tokens == 200 and row.output_tokens == 300 and row.status == "failed" for row in rows)


@pytest.mark.parametrize("target", [8, 13, 12001, 18000])
def test_integer_word_allocation_exact_and_positive(target):
    result, _ = normalize(ModelOutline.model_validate(outline(8)), target)
    assert sum(c["suggested_words"] for c in result) == target
    assert min(c["suggested_words"] for c in result) > 0


def test_json_fence_tolerated_but_invalid_field_types_rejected():
    assert len(parse_outline("```json\n" + json.dumps(outline()) + "\n```").full_outline) == 10
    data = outline()
    data["full_outline"][0]["suggested_words"] = "many"
    with pytest.raises(ValueError):
        parse_outline(json.dumps(data))


def test_chemical_data_names_are_not_confused_with_concrete_values():
    data = outline()
    data["full_outline"][0]["data_needed"] = ["CO2排放量", "H2产量"]
    _, notices = normalize(ModelOutline.model_validate(data), 12000)
    assert not any("数字" in notice for notice in notices)
    data["full_outline"][0]["key_points"] = ["总投资2.8亿元"]
    _, notices = normalize(ModelOutline.model_validate(data), 12000)
    assert any("数字" in notice for notice in notices)


def test_report_result_and_task_completion_share_a_transaction(setup, monkeypatch):
    report = make_report(setup)
    task = setup.api.post(f"/api/v1/reports/{report['id']}/outline/generate").json()

    def crash_before_finish(*_):
        raise RuntimeError("simulated commit interruption")

    monkeypatch.setattr(setup.pipeline, "finish", crash_before_finish)
    run_pending(setup)
    assert setup.api.get("/api/v1/reports/" + report["id"]).json()["outline"] == []
    assert setup.api.get("/api/v1/tasks/" + task["id"]).json()["status"] == "failed"


def test_report_budget_does_not_reset_with_new_gateway(setup):
    setup.settings.report_budget_yuan = 0.01
    setup.gateway.reserve(setup.settings.model_name, "test", 9000, "report-budget", 0)
    other = Gateway(setup.store, client_factory=lambda: setup.fake, sleep=lambda _: None)
    with pytest.raises(AppError, match="BUDGET_EXCEEDED"):
        other.reserve(setup.settings.model_name, "test", 2000, "report-budget", 0)
    assert other.budget()["used_yuan"] == 0.009


def test_browser_navigation_allowed_but_cross_site_mutations_rejected(setup):
    assert setup.api.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert (
        setup.api.post("/api/v1/reports", json={"title": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )


def test_ready_document_can_be_reindexed_without_reembedding(setup):
    response = setup.api.post("/api/v1/documents", files={"files": ("stable.txt", "公开合成项目资料".encode())})
    identity = response.json()["documents"][0]["document"]["id"]
    run_pending(setup)
    before = len(setup.fake.calls)
    assert setup.api.post(f"/api/v1/documents/{identity}/retry").status_code == 202
    run_pending(setup)
    assert len(setup.fake.calls) == before
    assert setup.api.get("/api/v1/documents").json()["documents"][0]["status"] == "ready"
