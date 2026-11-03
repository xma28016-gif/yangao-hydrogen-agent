import signal
import threading

from app.core.config import Settings
from app.models.tables import new_id
from app.repositories.store import Store
from app.services.model_gateway import Gateway
from app.services.pipeline import Pipeline


def main():
    settings = Settings()
    store = Store(settings)
    pipeline = Pipeline(store, Gateway(store))
    worker_id, stopping = new_id(), threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    while not stopping.is_set():
        task = pipeline.claim(worker_id)
        if task is None:
            stopping.wait(1)
            continue
        done = threading.Event()

        def heartbeat(task_id=task.id, completed=done):
            while not completed.wait(min(10, settings.lease_seconds / 3)):
                try:
                    pipeline.heartbeat(task_id, worker_id)
                except Exception:
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            pipeline.run(task, worker_id)
        finally:
            done.set()
            thread.join(timeout=2)
    store.engine.dispose()


if __name__ == "__main__":
    main()
