"""Worker Celery (LOFI_QUEUE_BACKEND=celery, Redis em LOFI_REDIS_URL).

    cd backend
    celery -A lofi_studio.celery_app worker --pool=solo --concurrency=1

`--pool=solo`: uma tarefa por vez por worker, que é o certo para uma GPU só (ComfyUI e Chatterbox
disputam a mesma VRAM). Para várias GPUs, rode um worker por máquina apontando para o mesmo Redis e banco
(PostgreSQL em LOFI_DATABASE_URL).
"""
from celery import Celery

from .config import get_settings

_settings = get_settings()
app = Celery("lofi_studio", broker=_settings.redis_url, backend=None)
app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_track_started=False,
                broker_connection_retry_on_startup=True)


@app.task(name="lofi_studio.run_job")
def run_job(job_id: int) -> None:
    from . import pipeline  # noqa: F401  registra os tipos de tarefa
    from .db import init_db
    from .jobs import execute, get_queue
    init_db()
    execute(job_id, get_queue())
