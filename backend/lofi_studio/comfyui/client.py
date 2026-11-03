"""Cliente da API HTTP/WebSocket oficial do ComfyUI (servidor em server.py do ComfyUI).

Endpoints usados: /system_stats, /object_info, /models, /models/{pasta}, /upload/image,
/prompt, /queue, /history/{id}, /view, /interrupt e o WebSocket /ws?clientId=.
"""
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyUnavailable(ComfyError):
    pass


class ComfyRejected(ComfyError):
    """O servidor recusou o workflow (node_errors / erro de validação)."""

    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


class ComfyExecutionError(ComfyError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.details = details


@dataclass
class Progress:
    prompt_id: str
    node: str | None = None
    value: int = 0
    max: int = 0
    phase: str = "running"   # queued, running, cached, done, error


ProgressCallback = Callable[[Progress], None]


class ComfyClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None,
                 client_id: str | None = None):
        u = urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"URL inválida para ComfyUI: {base_url}")
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or uuid.uuid4().hex
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- chamadas básicas ---------------------------------------------------
    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ComfyUnavailable(f"{self.base_url}: {e}") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text[:800]
            if path == "/prompt":
                raise ComfyRejected(f"workflow recusado ({r.status_code})", body)
            raise ComfyError(f"{method} {path} -> HTTP {r.status_code}: {body}")
        return r

    def system_stats(self) -> dict:
        return self._req("GET", "/system_stats").json()

    def object_info(self, node_class: str | None = None) -> dict:
        return self._req("GET", f"/object_info/{node_class}" if node_class else "/object_info").json()

    def model_folders(self) -> list[str]:
        return self._req("GET", "/models").json()

    def models(self, folder: str) -> list[str]:
        return self._req("GET", f"/models/{folder}").json()

    def queue(self) -> dict:
        return self._req("GET", "/queue").json()

    def history(self, prompt_id: str) -> dict:
        return self._req("GET", f"/history/{prompt_id}").json().get(prompt_id, {})

    def upload_image(self, path: Path, *, subfolder: str = "lofi", overwrite: bool = True) -> dict:
        path = Path(path)
        with path.open("rb") as fh:
            r = self._req("POST", "/upload/image",
                          files={"image": (path.name, fh, "image/png")},
                          data={"subfolder": subfolder, "type": "input", "overwrite": str(overwrite).lower()})
        info = r.json()
        # Valor aceito por LoadImage: "subpasta/arquivo.png"
        info["load_name"] = f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]
        return info

    def submit(self, workflow: dict) -> str:
        r = self._req("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        data = r.json()
        if data.get("node_errors"):
            raise ComfyRejected("workflow com erros de nó", data["node_errors"])
        if not data.get("prompt_id"):
            raise ComfyRejected("servidor não retornou prompt_id", data)
        return data["prompt_id"]

    def cancel(self, prompt_id: str) -> None:
        """Remove da fila se ainda não começou; interrompe se for o que está executando."""
        q = self.queue()
        running = {item[1] for item in q.get("queue_running", [])}
        if prompt_id in running:
            self._req("POST", "/interrupt", json={"prompt_id": prompt_id})
        else:
            self._req("POST", "/queue", json={"delete": [prompt_id]})

    def download(self, file: dict, dest_dir: Path) -> Path:
        r = self._req("GET", "/view", params={"filename": file["filename"],
                                              "subfolder": file.get("subfolder", ""),
                                              "type": file.get("type", "output")})
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / Path(file["filename"]).name
        out.write_bytes(r.content)
        return out

    # --- acompanhamento -----------------------------------------------------
    def wait(self, prompt_id: str, *, timeout: float = 600.0, on_progress: ProgressCallback | None = None,
             poll_interval: float = 1.0, use_websocket: bool = True,
             on_tick: Callable[[], None] | None = None) -> dict:
        """Espera o fim da execução e devolve a entrada do /history.

        Usa o WebSocket para progresso por passo quando disponível; caso contrário, consulta /history.
        `on_tick` é chamado periodicamente (amostragem de VRAM, checagem de cancelamento).
        """
        if use_websocket:
            try:
                return self._wait_ws(prompt_id, timeout, on_progress, on_tick)
            except ComfyExecutionError:
                raise
            except Exception:
                pass  # WebSocket indisponível: cai para polling
        return self._wait_poll(prompt_id, timeout, on_progress, poll_interval, on_tick)

    def _finished(self, prompt_id: str) -> dict | None:
        h = self.history(prompt_id)
        status = h.get("status") or {}
        if status.get("status_str") == "error":
            raise ComfyExecutionError("execução falhou no ComfyUI", _error_details(status))
        if h and (status.get("completed") or h.get("outputs")):
            return h
        return None

    def _wait_poll(self, prompt_id, timeout, on_progress, poll_interval, on_tick):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            done = self._finished(prompt_id)
            if done is not None:
                if on_progress:
                    on_progress(Progress(prompt_id, phase="done", value=1, max=1))
                return done
            if on_tick:
                on_tick()
            time.sleep(poll_interval)
        raise TimeoutError(f"ComfyUI não terminou {prompt_id} em {timeout:.0f}s")

    def _wait_ws(self, prompt_id, timeout, on_progress, on_tick):
        from websockets.sync.client import connect
        u = urlparse(self.base_url)
        ws_url = f"{'wss' if u.scheme == 'https' else 'ws'}://{u.netloc}{u.path}/ws?clientId={self.client_id}"
        deadline = time.monotonic() + timeout
        with connect(ws_url, open_timeout=5, max_size=None) as ws:
            # o prompt pode ter terminado antes da conexão abrir
            done = self._finished(prompt_id)
            if done is not None:
                return done
            while time.monotonic() < deadline:
                if on_tick:
                    on_tick()
                try:
                    raw = ws.recv(timeout=2.0)
                except TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue  # prévias binárias
                msg = json.loads(raw)
                kind, data = msg.get("type"), msg.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue
                if kind == "progress" and on_progress:
                    on_progress(Progress(prompt_id, data.get("node"), data.get("value", 0), data.get("max", 0)))
                elif kind == "execution_cached" and on_progress:
                    on_progress(Progress(prompt_id, phase="cached"))
                elif kind == "execution_error":
                    raise ComfyExecutionError(data.get("exception_message", "erro de execução"), data)
                elif kind == "execution_interrupted":
                    raise ComfyExecutionError("execução interrompida", data)
                elif kind in ("execution_success",) or (kind == "executing" and data.get("node") is None
                                                        and data.get("prompt_id") == prompt_id):
                    done = self._finished(prompt_id)
                    if done is not None:
                        if on_progress:
                            on_progress(Progress(prompt_id, phase="done", value=1, max=1))
                        return done
        raise TimeoutError(f"ComfyUI não terminou {prompt_id} em {timeout:.0f}s")


def _error_details(status: dict) -> Any:
    for name, data in status.get("messages", []):
        if name == "execution_error":
            return data
    return status


def output_files(history_entry: dict) -> list[dict]:
    """Lista arquivos produzidos (imagens, gifs/vídeos de nós como VHS_VideoCombine, áudios)."""
    files = []
    for node_id, out in (history_entry.get("outputs") or {}).items():
        for key in ("images", "gifs", "videos", "audio"):
            for f in out.get(key, []) or []:
                if isinstance(f, dict) and f.get("filename"):
                    files.append({**f, "node": node_id, "kind": key})
    return files
