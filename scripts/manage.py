"""Start/stop only processes created for this product; no reload supervisors."""

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "backend/.venv/bin/python"
RUN = ROOT / "data/run"


def start():
    RUN.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 18080)) == 0:
            raise SystemExit("18080 已占用，未停止任何现有服务")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    for name, args in [
        (
            "api",
            [
                "-m",
                "uvicorn",
                "app.main:app_factory",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "18080",
                "--no-access-log",
            ],
        ),
        ("worker", ["-m", "app.worker"]),
    ]:
        with (RUN / f"{name}.log").open("ab") as log:
            p = subprocess.Popen(
                [str(PY), *args], cwd=ROOT / "backend", env=env, stdout=log, stderr=log, start_new_session=True
            )
        (RUN / f"{name}.pid").write_text(str(p.pid))
        if name == "api":
            for _ in range(100):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18080/api/v1/health", timeout=1) as r:
                        if r.status == 200:
                            break
                except Exception:
                    if p.poll() is not None:
                        raise SystemExit("API 启动失败，请查看本机 data/run/api.log") from None
                    time.sleep(0.1)
            else:
                os.kill(p.pid, signal.SIGTERM)
                raise SystemExit("API 启动超时")
    print("研稿：http://127.0.0.1:18080（API + 独立任务 worker）")


def stop():
    for name, marker in [("worker", "app.worker"), ("api", "app.main:app_factory")]:
        path = RUN / f"{name}.pid"
        if not path.exists():
            continue
        pid = int(path.read_text())
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
        if marker in command and str(PY) in command:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(50):
                status = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
                ).stdout.strip()
                if not status or status.startswith("Z"):
                    break
                time.sleep(0.1)
            else:
                raise SystemExit("研稿仍在完成当前任务，请稍后再停止；PID记录已保留")
        path.unlink()
    print("已请求停止研稿进程，其他服务未受影响。")


if __name__ == "__main__":
    {"start": start, "stop": stop}[sys.argv[1]]()
