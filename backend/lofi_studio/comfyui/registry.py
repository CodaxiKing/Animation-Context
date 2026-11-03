"""Registro de instâncias ComfyUI: detecção, saúde, capacidades e escolha da instância."""
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .client import ComfyClient, ComfyError
from .dependencies import check
from .workflow import Workflow

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
CACHE_TTL_S = 120.0


@dataclass
class Instance:
    name: str
    url: str
    declared: bool = False          # veio da configuração (pode ser remoto) ou da varredura local
    online: bool = False
    last_error: str | None = None
    last_seen: float = 0.0
    system: dict = field(default_factory=dict)
    queue_remaining: int = 0
    _object_info: dict | None = None
    _object_info_at: float = 0.0
    _models: dict[str, list[str]] | None = None

    def public(self) -> dict:
        devices = self.system.get("devices", [])
        return {"name": self.name, "url": self.url, "declared": self.declared, "online": self.online,
                "last_error": self.last_error, "queue_remaining": self.queue_remaining,
                "comfyui_version": self.system.get("system", {}).get("comfyui_version"),
                "devices": [{"name": d.get("name"), "type": d.get("type"),
                             "vram_total_mb": round(d.get("vram_total", 0) / 2**20),
                             "vram_free_mb": round(d.get("vram_free", 0) / 2**20)} for d in devices]}


ClientFactory = Callable[[str], ComfyClient]


class Registry:
    def __init__(self, client_factory: ClientFactory | None = None):
        self._instances: dict[str, Instance] = {}
        self._lock = threading.Lock()
        self._factory = client_factory or (lambda url: ComfyClient(url, timeout=10))

    def client(self, inst: Instance) -> ComfyClient:
        return self._factory(inst.url)

    # --- cadastro -----------------------------------------------------------
    def add(self, name: str, url: str, *, declared: bool = True) -> Instance:
        host = urlparse(url).hostname
        if not declared and host not in LOCAL_HOSTS:
            raise ValueError("a detecção automática só registra instâncias locais")
        with self._lock:
            inst = self._instances.get(name) or Instance(name, url.rstrip("/"), declared)
            inst.url, inst.declared = url.rstrip("/"), declared or inst.declared
            self._instances[name] = inst
        return inst

    def remove(self, name: str) -> None:
        with self._lock:
            self._instances.pop(name, None)

    def all(self) -> list[Instance]:
        with self._lock:
            return list(self._instances.values())

    def get(self, name: str) -> Instance:
        with self._lock:
            if name not in self._instances:
                raise KeyError(f"instância ComfyUI desconhecida: {name}")
            return self._instances[name]

    def load_config(self, entries: list[str]) -> None:
        for i, entry in enumerate(entries):
            name, _, url = entry.partition("=") if "=" in entry else (f"comfy{i}", "", entry)
            self.add(name.strip(), url.strip(), declared=True)

    def discover(self, ports: list[int]) -> list[Instance]:
        """Varre 127.0.0.1 nas portas informadas e registra o que responder a /system_stats."""
        found = []
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            if any(i.url == url for i in self.all()):
                continue
            try:
                r = httpx.get(url + "/system_stats", timeout=0.6)
                if r.status_code == 200 and "system" in r.json():
                    found.append(self.add(f"local-{port}", url, declared=False))
            except (httpx.HTTPError, ValueError):
                continue
        return found

    # --- saúde e capacidades ------------------------------------------------
    def refresh(self, inst: Instance) -> Instance:
        try:
            with self.client(inst) as c:
                inst.system = c.system_stats()
                q = c.queue()
                inst.queue_remaining = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
            inst.online, inst.last_error, inst.last_seen = True, None, time.time()
        except ComfyError as e:
            inst.online, inst.last_error = False, str(e)
        return inst

    def refresh_all(self) -> list[Instance]:
        return [self.refresh(i) for i in self.all()]

    def object_info(self, inst: Instance, *, force: bool = False) -> dict:
        if force or inst._object_info is None or time.time() - inst._object_info_at > CACHE_TTL_S:
            with self.client(inst) as c:
                inst._object_info = c.object_info()
            inst._object_info_at = time.time()
            inst._models = None
        return inst._object_info

    def models(self, inst: Instance, *, force: bool = False) -> dict[str, list[str]]:
        if force or inst._models is None:
            with self.client(inst) as c:
                inst._models = {folder: c.models(folder) for folder in c.model_folders()}
        return inst._models

    def compatibility(self, inst: Instance, wf: Workflow) -> dict:
        report = check(wf, self.object_info(inst))
        report["instance"] = inst.name
        return report

    def pick(self, wf: Workflow, *, preferred: str | None = None) -> tuple[Instance, dict]:
        """Escolhe a instância online e compatível com menor fila. Levanta erro com os relatórios."""
        candidates = [self.get(preferred)] if preferred else self.all()
        reports = []
        best = None
        for inst in candidates:
            self.refresh(inst)
            if not inst.online:
                reports.append({"instance": inst.name, "compatible": False, "error": inst.last_error})
                continue
            try:
                rep = self.compatibility(inst, wf)
            except ComfyError as e:
                reports.append({"instance": inst.name, "compatible": False, "error": str(e)})
                continue
            reports.append(rep)
            if rep["compatible"] and (best is None or inst.queue_remaining < best[0].queue_remaining):
                best = (inst, rep)
        if best is None:
            raise NoCompatibleInstance(wf.name, reports)
        return best


class NoCompatibleInstance(ComfyError):
    def __init__(self, workflow: str, reports: list[dict]):
        super().__init__(f"nenhuma instância ComfyUI online e compatível com '{workflow}'")
        self.reports = reports


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        from ..config import get_settings
        _registry = Registry()
        _registry.load_config(get_settings().comfyui_instances)
    return _registry
