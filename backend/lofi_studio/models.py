"""Modelo de dados: séries, personagens, cenários, episódios, cenas, tarefas, assets e gerações."""
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


def json_field(default_factory=dict):
    return Field(default_factory=default_factory, sa_column=Column(JSON, nullable=False))


class Series(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    title: str
    genre: str = "suspense"
    style_preset: str = "dark_cinematic_lofi_2d"
    synopsis: str = ""
    motor_slug: str | None = None          # série importada de external/motor-lofi/series/<slug>
    # interface de sobrevivência: {"status": [...], "relogio": {...}, "missao": {...}, "cor": [r,g,b],
    #   "dialogo": true (caixa de diálogo com retrato no lugar da legenda), "efeitos": true (retícula/glitch automáticos)}
    interface: dict[str, Any] = json_field()
    created_at: datetime = Field(default_factory=now)


class Character(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("series_id", "slug"),)
    id: int | None = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series.id", index=True)
    slug: str
    name: str
    description: str = ""                  # aparência em inglês, usada nos prompts
    rig: dict[str, Any] = json_field()     # ficha travada no formato do motor (top, legs, hair...)
    locked: bool = True                    # ficha travada: o roteiro não redefine o visual
    pronoun: str | None = None             # "ele"/"ela" para resolver "Ela pega..." (cadastrado, nunca deduzido)
    reference_assets: list[int] = json_field(list)
    # voz: {"provider": "chatterbox", "sample_asset": id, "exaggeration": 0.5, "cfg_weight": 0.5,
    #       "temperature": 0.8, "seed": 7}. O personagem de slug "narrador" narra as linhas sem fala.
    voice: dict[str, Any] = json_field()


class Environment(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("series_id", "slug"),)
    id: int | None = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series.id", index=True)
    slug: str
    name: str
    description: str = ""
    motor_scene: str | None = None         # cenário desenhado por código no motor (ex.: "mercado")
    reference_assets: list[int] = json_field(list)


class EpisodeStatus(str, Enum):
    draft = "draft"
    planning = "planning"
    generating = "generating"
    review = "review"
    rendering = "rendering"
    done = "done"
    failed = "failed"


class Episode(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("series_id", "number"),)
    id: int | None = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series.id", index=True)
    number: int
    title: str = ""
    script: str = ""
    status: EpisodeStatus = EpisodeStatus.draft
    spec: dict[str, Any] = json_field()    # roteiro técnico no formato do lofi.render
    quality: dict[str, Any] = json_field()
    output_path: str | None = None
    created_at: datetime = Field(default_factory=now)


class SceneStatus(str, Enum):
    planned = "planned"
    generating = "generating"
    ready = "ready"
    approved = "approved"
    needs_fix = "needs_fix"
    failed = "failed"


class Scene(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="episode.id", index=True)
    index: int
    start: float
    end: float
    text: str = ""
    environment: str | None = None
    shot: dict[str, Any] = json_field()    # o plano correspondente em Episode.spec["shots"]
    status: SceneStatus = SceneStatus.planned
    review_notes: str = ""
    version: int = 1
    preview_path: str | None = None


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    status: JobStatus = Field(default=JobStatus.queued, index=True)
    progress: float = 0.0
    message: str = ""
    params: dict[str, Any] = json_field()
    result: dict[str, Any] = json_field()
    error: str | None = None
    attempts: int = 0
    cancel_requested: bool | None = None   # pedido de cancelamento (lido também por workers de outro processo)
    parent_id: int | None = Field(default=None, foreign_key="job.id")
    episode_id: int | None = Field(default=None, foreign_key="episode.id")
    scene_id: int | None = Field(default=None, foreign_key="scene.id")
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Asset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    series_id: int | None = Field(default=None, foreign_key="series.id", index=True)
    kind: str                               # character_sheet, environment, scene_frame, video, audio...
    path: str
    tags: list[str] = json_field(list)
    meta: dict[str, Any] = json_field()
    job_id: int | None = Field(default=None, foreign_key="job.id")
    created_at: datetime = Field(default_factory=now)


class GenerationRecord(SQLModel, table=True):
    """Trilha de auditoria de cada execução no ComfyUI: parâmetros, seed, tempo e VRAM."""
    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="job.id", index=True)
    instance: str
    workflow: str
    prompt_id: str | None = None
    seed: int | None = None
    params: dict[str, Any] = json_field()
    outputs: list[dict[str, Any]] = json_field(list)
    status: str = "queued"
    error: str | None = None
    attempt: int = 1
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    duration_s: float | None = None
    vram_used_peak_mb: float | None = None
    gpu_name: str | None = None
