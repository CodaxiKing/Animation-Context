"""Configuração por variáveis de ambiente (prefixo LOFI_) ou arquivo .env."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOFI_", env_file=ROOT / ".env", extra="ignore")

    # SQLite em desenvolvimento; em produção, por exemplo postgresql+psycopg://user:pass@host/db
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'studio.db').as_posix()}"
    data_dir: Path = ROOT / "data"
    motor_dir: Path = ROOT / "external" / "motor-lofi"
    workflows_dir: Path = ROOT / "workflows"

    # Instâncias ComfyUI declaradas explicitamente ("nome=url"). Hosts fora de localhost só entram por aqui.
    comfyui_instances: list[str] = Field(default_factory=list)
    # Portas locais varridas na detecção automática (somente 127.0.0.1).
    comfyui_scan_ports: list[int] = Field(default_factory=lambda: list(range(8188, 8193)))
    comfyui_timeout_s: float = 600.0
    comfyui_max_retries: int = 2
    comfyui_websocket: bool = True   # progresso por passo; False usa só polling de /history

    # Fila: "local" (threads, sem Redis) ou "celery" (requer celery + redis instalados).
    queue_backend: str = "local"
    queue_workers: int = 2
    redis_url: str = "redis://localhost:6379/0"

    ffmpeg: str = "ffmpeg"


@lru_cache
def get_settings() -> Settings:
    return Settings()
