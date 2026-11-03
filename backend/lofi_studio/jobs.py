"""Fila de tarefas persistida no banco.

Backend "local": threads no próprio processo da API, sem Redis. O estado de cada tarefa
fica na tabela `job`, então o painel acompanha tudo pelo banco, qualquer que seja o backend.
"""
import threading
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


def task(kind: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn
    return deco


class JobCancelled(RuntimeError):
    pass


class JobContext:
    def __init__(self, job: Job, queue: "LocalQueue"):
        self.job_id = job.id
        self.params: dict[str, Any] = dict(job.params)
        self.episode_id, self.scene_id = job.episode_id, job.scene_id
        self._queue = queue

    def progress(self, value: float, message: str = "") -> None:
        with session_scope() as s:
            job = s.get(Job, self.job_id)
            job.progress = max(0.0, min(1.0, value))
            if message:
                job.message = message
            s.add(job)
            s.commit()
        self.check_cancelled()

    def should_cancel(self) -> bool:
        return self.job_id in self._queue._cancel_flags

    def check_cancelled(self) -> None:
        if self.should_cancel():
            raise JobCancelled()

    def spawn(self, kind: str, params: dict, **links) -> int:
        return self._queue.submit(kind, params, parent_id=self.job_id, **links)


class LocalQueue:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="lofi-job")
        self._cancel_flags: set[int] = set()
        self._lock = threading.Lock()

    def submit(self, kind: str, params: dict | None = None, *, episode_id: int | None = None,
               scene_id: int | None = None, parent_id: int | None = None, attempts: int = 0) -> int:
        if kind not in _HANDLERS:
            raise ValueError(f"tipo de tarefa desconhecido: {kind}")
        with session_scope() as s:
            job = Job(kind=kind, params=params or {}, episode_id=episode_id, scene_id=scene_id,
                      parent_id=parent_id, attempts=attempts)
            s.add(job)
            s.commit()
            job_id = job.id
        self._pool.submit(self._run, job_id)
        return job_id

    def cancel(self, job_id: int) -> Job:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in (JobStatus.queued, JobStatus.running):
                with self._lock:
                    self._cancel_flags.add(job_id)
                if job.status == JobStatus.queued:
                    job.status, job.finished_at = JobStatus.cancelled, datetime.now(timezone.utc)
                    s.add(job)
                    s.commit()
            s.refresh(job)
            return job

    def retry(self, job_id: int) -> int:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in (JobStatus.failed, JobStatus.cancelled):
                raise ValueError("só tarefas com falha ou canceladas podem ser repetidas")
            return self.submit(job.kind, job.params, episode_id=job.episode_id, scene_id=job.scene_id,
                               parent_id=job.parent_id, attempts=job.attempts + 1)

    def _run(self, job_id: int) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job.status != JobStatus.queued:
                return
            job.status, job.started_at = JobStatus.running, datetime.now(timezone.utc)
            s.add(job)
            s.commit()
            s.refresh(job)
            ctx = JobContext(job, self)
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
        finally:
            with self._lock:
                self._cancel_flags.discard(job_id)
        with session_scope() as s:
            job = s.get(Job, job_id)
            job.status, job.result, job.error = status, result, error
            job.finished_at = datetime.now(timezone.utc)
            if status == JobStatus.succeeded:
                job.progress = 1.0
            s.add(job)
            s.commit()

    def shutdown(self, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=True)


def recover_interrupted() -> int:
    """Tarefas que estavam rodando quando o processo caiu viram 'failed' (podem ser repetidas)."""
    with session_scope() as s:
        stale = s.exec(select(Job).where(Job.status.in_([JobStatus.running, JobStatus.queued]))).all()
        for job in stale:
            job.status, job.error = JobStatus.failed, "interrompida: o servidor reiniciou"
            job.finished_at = datetime.now(timezone.utc)
            s.add(job)
        s.commit()
        return len(stale)


_queue: LocalQueue | None = None


def get_queue() -> LocalQueue:
    global _queue
    if _queue is None:
        from .config import get_settings
        settings = get_settings()
        if settings.queue_backend != "local":
            raise NotImplementedError(
                f"backend de fila '{settings.queue_backend}' ainda não implementado; use LOFI_QUEUE_BACKEND=local")
        _queue = LocalQueue(settings.queue_workers)
    return _queue
