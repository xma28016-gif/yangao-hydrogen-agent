"""Hook for verify.sh: actual HTTP persistence, no model and no production data."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(os.environ["RESTART_CHECK_DIR"])
STATE = TEMP / "hook.json"
PY = ROOT / "backend/.venv/bin/python"


def request(port, path, body=None, method=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


def main():
    action = sys.argv[1]
    if action == "start":
        if STATE.exists():
            port = json.loads(STATE.read_text())["port"]
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit("重启检查端口已占用，未停止任何服务")
        env = dict(
            os.environ,
            DATA_DIR=str(TEMP / "data"),
            MODEL_API_KEY="",
            PYTHONPATH=str(ROOT / "backend"),
        )
        with (TEMP / "server.log").open("ab") as log:
            proc = subprocess.Popen(
                [
                    str(PY),
                    "-m",
                    "uvicorn",
                    "app.main:app_factory",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT / "backend",
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        STATE.write_text(json.dumps({"port": port, "pid": proc.pid}))
        for _ in range(100):
            try:
                request(port, "/health")
                print(proc.pid)
                return
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("重启检查实例启动失败") from None
                time.sleep(0.1)
        proc.terminate()
        raise SystemExit("重启检查实例启动超时")
    state = json.loads(STATE.read_text())
    if action == "write":
        # Creation requires a ready source; this isolated fixture is explicitly not a model result.
        sys.path.insert(0, str(ROOT / "backend"))
        from app.core.config import Settings
        from app.models.tables import ChapterRecord, Document, Report
        from app.repositories.store import Store

        cfg = Settings(_env_file=None, data_dir=TEMP / "data", model_api_key="")
        store = Store(cfg)
        with store.session(write=True) as s:
            s.add(
                Document(
                    owner_id=cfg.owner_id,
                    filename="重启测试占位资料",
                    stored_name="fixture",
                    sha256="restart-fixture",
                    extension=".txt",
                    size=0,
                    status="ready",
                    chunks_count=0,
                )
            )
        result = request(state["port"], "/reports", {"title": sys.argv[2]})
        # Explicit persistence fixture, no model call or model-quality claim.
        with store.session(write=True) as s:
            report = store.get(s, Report, result["id"])
            report.status = "completed"
            report.truth_base = {
                "validated_data": {"total_investment": None},
                "sources": {},
            }
            s.add(
                ChapterRecord(
                    owner_id=cfg.owner_id,
                    report_id=report.id,
                    chapter_id=1,
                    title="隔离重启检查",
                    suggested_words=1000,
                    max_words=1100,
                    phase="done",
                    status="ready",
                    body="重启原稿",
                    original_body="重启原稿",
                )
            )
        request(
            state["port"],
            f"/reports/{result['id']}/chapters/1",
            {"body": sys.argv[2], "version": 1},
            method="PUT",
        )
        store.engine.dispose()
        print(result["id"])
    elif action == "read":
        sys.path.insert(0, str(ROOT / "backend"))
        from app.core.config import Settings
        from app.models.tables import Report
        from app.repositories.store import Store
        from app.services.writing import chapters

        content = request(state["port"], f"/reports/{sys.argv[2]}/content")
        title = request(state["port"], f"/reports/{sys.argv[2]}")["title"]
        store = Store(
            Settings(_env_file=None, data_dir=TEMP / "data", model_api_key="")
        )
        with store.session() as s:
            report = store.get(s, Report, sys.argv[2])
            assert report.truth_base == {
                "validated_data": {"total_investment": None},
                "sources": {},
            }
            assert chapters(store, s, report.id)[0].original_body == "重启原稿"
        store.engine.dispose()
        assert (
            content["chapters"][0]["body"] == title and content["chapters"][0]["edited"]
        )
        print(content["chapters"][0]["body"])
    elif action == "stop":
        # verify.sh owns the kill/restart steps; don't signal PIDs that may have been reused.
        print("重启验证临时实例已交由 verify.sh 清理")


if __name__ == "__main__":
    main()
