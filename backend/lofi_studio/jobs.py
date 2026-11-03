"""Fila de tarefas persistida no banco.

Backends (LOFI_QUEUE_BACKEND):
  local   threads no próprio processo da API, sem Redis (padrão).
  celery  Celery + Redis: a API só enfileira; os workers executam
          (`celery -A lofi_studio.celery_app worker --pool=solo` na pasta backend).

O estado de cada tarefa fica na tabela `job`, então o painel acompanha tudo pelo banco,
qualquer que seja o backend. O cancelamento também passa pelo banco (cancel_requested).
"""
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from .db import session_scope
from .models import Job, JobStatus

Handler = Callable[["JobContext"], dict | None]
_HANDLERS: dict[str, Handler] = {}
CANCEL_CHECK_S = 2.0


def task(kind: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return deco


class JobCancelled(RuntimeError):
    pass


class JobContext:
    def __init__(self, job: Job, queue: "BaseQueue", local_flags: set[int] | None = None):
        self.job_id = job.id
        self.params: dict[str, Any] = dict(job.params)
        self.episode_id, self.scene_id = job.episode_id, job.scene_id
        self._queue = queue
        self._flags = local_flags if local_flags is not None else set()
        self._checked_at, self._cancel = 0.0, False

    def progress(self, value: float, message: str = "") -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            job.progress = max(0.0, min(1.0, value))
            if message:
                job.message = message
            self._cancel = bool(job.cancel_requested)
            self._checked_at = time.monotonic()
            s.add(job)
            s.commit()
        self.check_cancelled()

    def should_cancel(self) -> bool:
        if self.job_id in self._flags or self._cancel:
            return True
        if time.monotonic() - self._checked_at > CANCEL_CHECK_S:      # outro processo pode ter pedido
            with session_scope() as s:
                self._cancel = bool(s.get(Job, self.job_id).cancel_requested)
            self._checked_at = time.monotonic()
        return self._cancel

    def check_cancelled(self) -> None:
        if self.should_cancel():
            raise JobCancelled()

    def spawn(self, kind: str, params: dict, **links) -> int:
        return self._queue.submit(kind, params, parent_id=self.job_id, **links)


def execute(job_id: int, queue: "BaseQueue", local_flags: set[int] | None = None) -> None:
    """Roda uma tarefa e grava o resultado (usado pelos dois backends)."""
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None or job.status != JobStatus.queued:
            return
        job.status, job.started_at = JobStatus.running, datetime.now(timezone.utc)
        s.add(job)
        s.commit()
        s.refresh(job)
        ctx = JobContext(job, queue, local_flags)
        handler = _HANDLERS[job.kind]
    status, result, error = JobStatus.succeeded, {}, None
    try:
        ctx.check_cancelled()
        result = handler(ctx) or {}
    except JobCancelled:
        status = JobStatus.cancelled
    except Exception as e:  # registra qualquer falha na tarefa, sem derrubar o worker
        status, error = JobStatus.failed, f"{type(e).__name__}: {e}"
        result = {"traceback": traceback.format_exc()[-4000:],
                  **({"reports": e.reports} if hasattr(e, "reports") else {})}
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.status, job.result, job.error = status, result, error
        job.finished_at = datetime.now(timezone.utc)
        if status == JobStatus.succeeded:
            job.progress = 1.0
        s.add(job)
        s.commit()


class BaseQueue:
    def _create(self, kind: str, params: dict | None, episode_id, scene_id, parent_id, attempts) -> int:
        if kind not in _HANDLERS:
            raise ValueError(f"tipo de tarefa desconhecido: {kind}")
        with session_scope() as s:
            job = Job(kind=kind, params=params or {}, episode_id=episode_id, scene_id=scene_id,
                      parent_id=parent_id, attempts=attempts)
            s.add(job)
            s.commit()
            return job.id

    def submit(self, kind: str, params: dict | None = None, *, episode_id: int | None = None,
               scene_id: int | None = None, parent_id: int | None = None, attempts: int = 0) -> int:
        job_id = self._create(kind, params, episode_id, scene_id, parent_id, attempts)
        self._dispatch(job_id)
        return job_id

    def _dispatch(self, job_id: int) -> None:
        raise NotImplementedError

    def cancel(self, job_id: int) -> Job:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in (JobStatus.queued, JobStatus.running):
                job.cancel_requested = True
                self._flag(job_id)
                if job.status == JobStatus.queued:
                    job.status, job.finished_at = JobStatus.cancelled, datetime.now(timezone.utc)
                s.add(job)
                s.commit()
            s.refresh(job)
            return job

    def _flag(self, job_id: int) -> None:
        pass

    def retry(self, job_id: int) -> int:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in (JobStatus.failed, JobStatus.cancelled):
                raise ValueError("só tarefas com falha ou canceladas podem ser repetidas")
            return self.submit(job.kind, job.params, episode_id=job.episode_id, scene_id=job.scene_id,
                               parent_id=job.parent_id, attempts=job.attempts + 1)

    def shutdown(self, wait: bool = False) -> None:
        pass


class LocalQueue(BaseQueue):
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lofi-job")
        self._cancel_flags: set[int] = set()
        self._lock = threading.Lock()

    def _dispatch(self, job_id: int) -> None:
        self._pool.submit(self._run, job_id)

    def _flag(self, job_id: int) -> None:
        with self._lock:
            self._cancel_flags.add(job_id)

    def _run(self, job_id: int) -> None:
        try:
            execute(job_id, self, self._cancel_flags)
        finally:
            with self._lock:
                self._cancel_flags.discard(job_id)

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


class CeleryQueue(BaseQueue):
    """Enfileira no Celery (Redis); o worker chama `execute` com o mesmo banco."""

    def __init__(self):
        from .celery_app import run_job
        self._run_job = run_job

    def _dispatch(self, job_id: int) -> None:
        self._run_job.delay(job_id)


def recover_interrupted() -> int:
    """Backend local: tarefas que estavam rodando quando o processo caiu viram 'failed' (podem ser repetidas).
    Com Celery, os workers são outros processos: nada a recuperar aqui."""
    from .config import get_settings
    if get_settings().queue_backend != "local":
        return 0
    with session_scope() as s:
        stale = s.exec(select(Job).where(Job.status.in_([JobStatus.running, JobStatus.queued]))).all()
        for job in stale:
            job.status, job.error = JobStatus.failed, "interrompida: o servidor reiniciou"
            job.finished_at = datetime.now(timezone.utc)
            s.add(job)
        s.commit()
        return len(stale)


_queue: BaseQueue | None = None


def get_queue() -> BaseQueue:
    global _queue
    if _queue is None:
        from .config import get_settings
        settings = get_settings()
        if settings.queue_backend == "local":
            _queue = LocalQueue(settings.queue_workers)
        elif settings.queue_backend == "celery":
            _queue = CeleryQueue()
        else:
            raise ValueError(f"LOFI_QUEUE_BACKEND inválido: {settings.queue_backend} (use local ou celery)")
    return _queue
