"""Regression tests: synthetic records stay in temporary test repos, never real evidence."""

import subprocess
from pathlib import Path

import pytest
from app.core.config import AppError
from app.services.local_embedding import windows
from app.services.model_gateway import Gateway
from pydantic import SecretStr


def test_deepseek_request_uses_correct_non_thinking_switch(setup):
    setup.settings.model_provider = "deepseek"
    setup.settings.model_name = "deepseek-flash"
    setup.settings.model_base_url = "https://api.deepseek.com"
    setup.gateway.outline([{"role": "user", "content": "JSON outline"}], None)
    request = setup.fake.calls[-1][1]
    assert request["model"] == "deepseek-flash"
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_provider_mismatch_blocks_credential_transmission(setup):
    setup.settings.model_provider = "deepseek"
    setup.settings.model_name = "deepseek-flash"
    with pytest.raises(AppError, match="MODEL_PROVIDER_MISMATCH"):
        setup.gateway.outline([{"role": "user", "content": "JSON"}], None)
    assert setup.fake.calls == []


def test_authentication_errors_do_not_burn_retry_budget(setup):
    attempts = []

    class AuthenticationFailure(Exception):
        status_code = 401

    def fail(_):
        attempts.append(1)
        raise AuthenticationFailure("untrusted upstream text")

    with pytest.raises(AppError, match="MODEL_AUTHENTICATION_FAILED"):
        setup.gateway.invoke(fail, lambda _: None, model=setup.settings.model_name, step="test", input_bytes=10)
    assert len(attempts) == 1


def test_local_embedding_needs_no_api_key_and_has_no_cloud_fee(setup):
    setup.settings.embedding_provider = "local"
    setup.settings.embedding_dimensions = 512
    setup.settings.embedding_model = "BAAI/bge-small-zh-v1.5"
    setup.settings.model_api_key = SecretStr("")

    class OfflineEncoder:
        def embed(self, texts, query=False):
            return [[1.0] + [0.0] * 511 for _ in texts]

    gateway = Gateway(setup.store, local_encoder=OfflineEncoder())
    assert len(gateway.embeddings(["公开合成资料"])[0]) == 512
    assert gateway.budget()["used_yuan"] == 0
    assert setup.fake.calls == []


def test_local_windows_preserve_all_content_below_tokenizer_limit():
    text = ("中文长资料 CO2 hydrogen。" * 200) + "末尾不能丢失"
    parts = windows(text)
    assert "".join(parts) == text
    assert max(len(p.encode()) for p in parts) <= 400


@pytest.mark.parametrize(
    "record,expected",
    [
        ("| 2026-11-03 12:00:00 | 大纲 | - | 1 | 1 | - | 1s | 失败 | 0 | 失败 |\n", "待验"),
        ("| 2026-11-03 12:00:00 | 大纲 | - | 1 | 1 | - | 1s | 合规 | 0 | 通过 |\n", "有记录"),
        (
            "| 2026-11-03 12:00:00 | 大纲 | - | 1 | 1 | - | 1s | 失败 | 0 | 失败 |\n## 运行 新一轮\n| 2026-11-03 13:00:00 | 大纲 | - | 1 | 1 | - | 1s | 合规 | 0 | 通过 |\n- 本轮结果：通过\n",
            "有记录",
        ),
        (
            "| 2026-11-03 12:00:00 | 大纲 | - | 1 | 1 | - | 1s | 合规 | 0 | 通过 |\n## 运行 失败尝试\n- 本轮结果：失败\n",
            "待验",
        ),
    ],
)
def test_verifier_judges_results_not_record_existence(tmp_path, record, expected):
    verifier = Path(__file__).resolve().parent / "support/verify-toolkit.sh"
    script = tmp_path / "verify.sh"
    script.write_bytes(verifier.read_bytes())
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    evidence = tmp_path / "projects/probe/docs/evidence/阶段2-1"
    evidence.mkdir(parents=True)
    (evidence / "冒烟记录.md").write_text("# Synthetic verifier fixture\n- 模型：offline-test-only\n" + record)
    result = subprocess.run(
        ["bash", str(script), "probe", "阶段2-1"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    row = next(line for line in result.stdout.splitlines() if line.startswith("是否表第 3 项"))
    assert expected in row
