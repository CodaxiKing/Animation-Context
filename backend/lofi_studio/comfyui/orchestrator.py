"""Execução ponta a ponta de um workflow: escolha de instância, upload de referências,
envio, progresso, cancelamento, novas tentativas, download e registro de auditoria."""
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import session_scope
from ..models import GenerationRecord
from .client import ComfyExecutionError, ComfyRejected, ComfyUnavailable, Progress, output_files
from .registry import Registry, get_registry
from .workflow import Workflow

VRAM_SAMPLE_EVERY_S = 2.0


class Cancelled(RuntimeError):
    pass


@dataclass
class RunResult:
    files: list[Path]
    seed: int
    instance: str
    prompt_id: str
    record_id: int
    duration_s: float
    vram_used_peak_mb: float | None


def run_workflow(wf: Workflow, params: dict[str, Any], out_dir: Path, *,
                 images: dict[str, Path] | None = None, seed: int | None = None,
                 instance: str | None = None, job_id: int | None = None,
                 max_retries: int = 2, timeout: float = 600.0,
                 on_progress: Callable[[Progress], None] | None = None,
                 should_cancel: Callable[[], bool] | None = None,
                 registry: Registry | None = None, use_websocket: bool = True) -> RunResult:
    registry = registry or get_registry()
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        inst, _ = registry.pick(wf, preferred=instance)
        record_id = None
        try:
            with registry.client(inst) as client:
                values = dict(params)
                for key, path in (images or {}).items():
                    values[key] = client.upload_image(Path(path))["load_name"]
                graph, used_seed = wf.render(values, seed=seed)
                record_id = _record(job_id=job_id, instance=inst.name, workflow=wf.name, seed=used_seed,
                                    params={k: v for k, v in values.items() if k != "seed"}, attempt=attempt)
                started = time.monotonic()
                prompt_id = client.submit(graph)
                _update(record_id, prompt_id=prompt_id, status="running")

                vram = _VramSampler(client)
                def tick():
                    vram.sample()
                    if should_cancel and should_cancel():
                        client.cancel(prompt_id)
                        raise Cancelled("cancelado pelo usuário")

                entry = client.wait(prompt_id, timeout=timeout, on_progress=on_progress,
                                    on_tick=tick, use_websocket=use_websocket)
                vram.sample(force=True)
                files = [client.download(f, out_dir) for f in output_files(entry)]
                duration = time.monotonic() - started
                if not files:
                    raise ComfyExecutionError("workflow terminou sem arquivos de saída (falta um nó Save?)")
                _update(record_id, status="succeeded", outputs=[{"path": str(p)} for p in files],
                        finished_at=datetime.now(timezone.utc), duration_s=round(duration, 3),
                        vram_used_peak_mb=vram.peak_mb, gpu_name=vram.gpu)
                return RunResult(files, used_seed, inst.name, prompt_id, record_id, duration, vram.peak_mb)
        except Cancelled:
            if record_id:
                _update(record_id, status="cancelled", finished_at=datetime.now(timezone.utc))
            raise
        except ComfyRejected as e:
            if record_id:
                _update(record_id, status="rejected", error=f"{e}: {e.details}", finished_at=datetime.now(timezone.utc))
            raise  # workflow incompatível: repetir não adianta
        except (ComfyUnavailable, ComfyExecutionError, TimeoutError) as e:
            last_error = e
            if record_id:
                details = getattr(e, "details", None)
                _update(record_id, status="failed", error=f"{e}" + (f": {details}" if details else ""),
                        finished_at=datetime.now(timezone.utc))
            # seed fixada pelo usuário se mantém; sem seed, cada tentativa sorteia outra
    raise last_error  # type: ignore[misc]


class _VramSampler:
    def __init__(self, client):
        self.client, self.peak_mb, self.gpu, self._last = client, None, None, 0.0

    def sample(self, force: bool = False):
        if not force and time.monotonic() - self._last < VRAM_SAMPLE_EVERY_S:
            return
        self._last = time.monotonic()
        try:
            devices = self.client.system_stats().get("devices", [])
        except Exception:
            return
        gpus = [d for d in devices if d.get("type") not in ("cpu", None)] or devices
        if not gpus:
            return
        d = gpus[0]
        self.gpu = d.get("name")
        used = (d.get("vram_total", 0) - d.get("vram_free", 0)) / 2**20
        self.peak_mb = round(max(self.peak_mb or 0, used), 1)


def _record(**fields) -> int:
    with session_scope() as s:
        rec = GenerationRecord(**fields)
        s.add(rec)
        s.commit()
        return rec.id


def _update(record_id: int, **fields) -> None:
    with session_scope() as s:
        rec = s.get(GenerationRecord, record_id)
        for k, v in fields.items():
            setattr(rec, k, v)
        s.add(rec)
        s.commit()
