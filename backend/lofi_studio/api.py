"""API HTTP do estúdio (FastAPI). Rode com: uvicorn lofi_studio.api:app --reload --app-dir backend"""
import io
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from . import __version__, engines, motor, pipeline, style_master
from .comfyui import ComfyError, WorkflowError, get_registry, list_workflows, load
from .config import get_settings
from .db import get_session, init_db
from .jobs import get_queue, recover_interrupted
from .models import (Asset, Character, Environment, Episode, GenerationRecord, Job, Scene, SceneStatus, Series)

SLUG = r"^[a-z0-9][a-z0-9_-]{0,63}$"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_interrupted()
    yield
    get_queue().shutdown()


app = FastAPI(title="Motor Lo-Fi V5", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
api = APIRouter(prefix="/api")
SessionDep = Depends(get_session)


def _get(s: Session, model, obj_id: int):
    obj = s.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {obj_id} não encontrado")
    return obj


# --- sistema --------------------------------------------------------------------
@app.get("/health")
def health():
    try:
        motor_ok, scenes = True, len(motor.available_scenes())
    except motor.MotorUnavailable as e:
        motor_ok, scenes = str(e), 0
    return {"ok": True, "version": __version__, "motor": motor_ok, "motor_scenes": scenes,
            "ffmpeg": motor.ffmpeg_available()}


@api.get("/engines")
def list_engines():
    return engines.catalog()


@api.get("/styles")
def list_styles():
    return [p.public() for p in style_master.presets().values()]


class PromptPreview(BaseModel):
    kind: str = Field(pattern="^(character|character_sheet|environment)$")
    description: str
    view: str = "front"
    emotion: str = "neutral"


@api.post("/styles/{preset_id}/prompt")
def preview_prompt(preset_id: str, body: PromptPreview):
    try:
        preset = style_master.get(preset_id)
        if body.kind == "character":
            return style_master.character_prompt(preset, body.description, view=body.view, emotion=body.emotion)
        if body.kind == "character_sheet":
            return style_master.character_sheet_prompt(preset, body.description)
        return style_master.environment_prompt(preset, body.description)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e)) from e


# --- séries ---------------------------------------------------------------------
class SeriesIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    title: str
    genre: str = "suspense"
    style_preset: str = "dark_cinematic_lofi_2d"
    synopsis: str = ""


@api.get("/series")
def list_series(s: Session = SessionDep):
    return s.exec(select(Series)).all()


@api.post("/series", status_code=201)
def create_series(body: SeriesIn, s: Session = SessionDep):
    if body.style_preset not in style_master.presets():
        raise HTTPException(422, f"preset desconhecido: {body.style_preset}")
    if s.exec(select(Series).where(Series.slug == body.slug)).first():
        raise HTTPException(409, "slug já existe")
    series = Series(**body.model_dump())
    s.add(series)
    s.commit()
    s.refresh(series)
    return series


@api.get("/series/{series_id}")
def get_series(series_id: int, s: Session = SessionDep):
    return _get(s, Series, series_id)


@api.get("/motor/series")
def motor_series():
    return motor.list_motor_series()


@api.post("/motor/series/{slug}/import", status_code=201)
def import_series(slug: str, s: Session = SessionDep):
    if not re.match(SLUG, slug):
        raise HTTPException(422, "slug inválido")
    try:
        return pipeline.import_motor_series(s, slug)
    except FileNotFoundError as e:
        raise HTTPException(404, f"série não existe no motor: {slug}") from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


# --- personagens e cenários -------------------------------------------------------
class CharacterIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    name: str
    description: str = ""
    rig: dict[str, Any] = {}
    locked: bool = True


class CharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    rig: dict[str, Any] | None = None
    locked: bool | None = None
    unlock: bool = False   # alterar uma ficha travada exige unlock=true explícito


@api.get("/series/{series_id}/characters")
def list_characters(series_id: int, s: Session = SessionDep):
    return s.exec(select(Character).where(Character.series_id == series_id)).all()


@api.post("/series/{series_id}/characters", status_code=201)
def create_character(series_id: int, body: CharacterIn, s: Session = SessionDep):
    _get(s, Series, series_id)
    if s.exec(select(Character).where(Character.series_id == series_id, Character.slug == body.slug)).first():
        raise HTTPException(409, "personagem já existe")
    ch = Character(series_id=series_id, **body.model_dump())
    s.add(ch)
    s.commit()
    s.refresh(ch)
    return ch


@api.patch("/characters/{character_id}")
def update_character(character_id: int, body: CharacterPatch, s: Session = SessionDep):
    ch = _get(s, Character, character_id)
    changes = body.model_dump(exclude_unset=True, exclude={"unlock"})
    visual = {"description", "rig"} & set(changes)
    if ch.locked and visual and not body.unlock:
        raise HTTPException(423, "ficha travada: envie unlock=true para alterar o visual de propósito")
    for k, v in changes.items():
        setattr(ch, k, v)
    s.add(ch)
    s.commit()
    s.refresh(ch)
    return ch


class EnvironmentIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    name: str
    description: str = ""
    motor_scene: str | None = None


@api.get("/series/{series_id}/environments")
def list_environments(series_id: int, s: Session = SessionDep):
    return s.exec(select(Environment).where(Environment.series_id == series_id)).all()


@api.post("/series/{series_id}/environments", status_code=201)
def create_environment(series_id: int, body: EnvironmentIn, s: Session = SessionDep):
    _get(s, Series, series_id)
    if body.motor_scene and body.motor_scene not in motor.available_scenes():
        raise HTTPException(422, f"cenário do motor desconhecido: {body.motor_scene}")
    env = Environment(series_id=series_id, **body.model_dump())
    s.add(env)
    s.commit()
    s.refresh(env)
    return env


@api.get("/motor/scenes")
def motor_scenes():
    return motor.available_scenes()


class GenerateArt(BaseModel):
    workflow: str | None = None
    seed: int | None = None
    instance: str | None = None
    time_of_day: str = "night"
    weather: str = ""


@api.post("/characters/{character_id}/generate-sheet", status_code=202)
def generate_character_sheet(character_id: int, body: GenerateArt, s: Session = SessionDep):
    _get(s, Character, character_id)
    params = {"character_id": character_id, **body.model_dump(exclude_none=True)}
    return {"job_id": get_queue().submit("character_sheet", params)}


@api.post("/characters/{character_id}/reference", status_code=201)
async def upload_reference(character_id: int, file: UploadFile, s: Session = SessionDep):
    """Envia uma imagem de referência; ela vira a âncora de identidade (IPAdapter) do personagem."""
    ch = _get(s, Character, character_id)
    series = s.get(Series, ch.series_id)
    data = await file.read()
    if len(data) > 20 * 2**20:
        raise HTTPException(413, "imagem maior que 20 MB")
    try:
        im = Image.open(io.BytesIO(data))
        im.verify()
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        raise HTTPException(422, "arquivo não é uma imagem válida") from e
    dest = pipeline.series_dir(series) / "assets" / "references" / f"{ch.slug}_{len(ch.reference_assets) + 1}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    asset = Asset(series_id=series.id, kind="character_reference", path=str(dest), tags=[ch.slug],
                  meta={"original_name": file.filename})
    s.add(asset)
    s.flush()
    ch.reference_assets = [asset.id, *ch.reference_assets]
    s.add(ch)
    s.commit()
    s.refresh(ch)
    return ch


class AnchorIn(BaseModel):
    asset_id: int


@api.post("/characters/{character_id}/anchor")
def set_anchor(character_id: int, body: AnchorIn, s: Session = SessionDep):
    """Escolhe qual referência do personagem é a âncora de identidade (a primeira da lista)."""
    ch = _get(s, Character, character_id)
    if body.asset_id not in ch.reference_assets:
        raise HTTPException(422, "o asset não é uma referência deste personagem")
    ch.reference_assets = [body.asset_id, *[a for a in ch.reference_assets if a != body.asset_id]]
    s.add(ch)
    s.commit()
    s.refresh(ch)
    return ch


@api.post("/environments/{environment_id}/generate-art", status_code=202)
def generate_environment_art(environment_id: int, body: GenerateArt, s: Session = SessionDep):
    _get(s, Environment, environment_id)
    params = {"environment_id": environment_id, **body.model_dump(exclude_none=True)}
    return {"job_id": get_queue().submit("environment_art", params)}


# --- episódios e cenas ------------------------------------------------------------
class EpisodeIn(BaseModel):
    number: int = Field(ge=1, le=999)
    title: str = ""
    script: str = Field(min_length=1)


@api.get("/series/{series_id}/episodes")
def list_episodes(series_id: int, s: Session = SessionDep):
    return s.exec(select(Episode).where(Episode.series_id == series_id).order_by(Episode.number)).all()


@api.post("/series/{series_id}/episodes", status_code=201)
def create_episode(series_id: int, body: EpisodeIn, s: Session = SessionDep):
    _get(s, Series, series_id)
    if s.exec(select(Episode).where(Episode.series_id == series_id, Episode.number == body.number)).first():
        raise HTTPException(409, "episódio já existe")
    ep = Episode(series_id=series_id, **body.model_dump())
    s.add(ep)
    s.commit()
    s.refresh(ep)
    return ep


@api.get("/episodes/{episode_id}")
def get_episode(episode_id: int, s: Session = SessionDep):
    return _get(s, Episode, episode_id)


class GenerateEpisode(BaseModel):
    duration: float | None = Field(default=None, gt=0, le=3600)


@api.post("/episodes/{episode_id}/generate", status_code=202)
def generate_episode(episode_id: int, body: GenerateEpisode, s: Session = SessionDep):
    _get(s, Episode, episode_id)
    return {"job_id": get_queue().submit("generate_episode", body.model_dump(exclude_none=True),
                                         episode_id=episode_id)}


@api.get("/episodes/{episode_id}/keyframes")
def keyframe_plan(episode_id: int, s: Session = SessionDep):
    """Quais fundos e poses cada cena precisa, e quais ainda não existem na biblioteca da série."""
    return pipeline.keyframe_plan(s, _get(s, Episode, episode_id))


class KeyframesIn(BaseModel):
    seed: int | None = None
    instance: str | None = None
    time_of_day: str = "night"
    only: list[str] | None = None          # gerar só estas imagens
    character_workflow: str = Field(default="character_pose", pattern=SLUG)
    environment_workflow: str = Field(default="environment", pattern=SLUG)


@api.post("/episodes/{episode_id}/keyframes", status_code=202)
def generate_keyframes(episode_id: int, body: KeyframesIn, s: Session = SessionDep):
    ep = _get(s, Episode, episode_id)
    if not s.exec(select(Scene).where(Scene.episode_id == ep.id)).first():
        raise HTTPException(409, "gere o episódio (planejamento das cenas) antes das imagens-chave")
    return {"job_id": get_queue().submit("generate_keyframes", body.model_dump(exclude_none=True),
                                         episode_id=episode_id)}


@api.get("/episodes/{episode_id}/scenes")
def list_scenes(episode_id: int, s: Session = SessionDep):
    return s.exec(select(Scene).where(Scene.episode_id == episode_id).order_by(Scene.index)).all()


class SceneReview(BaseModel):
    status: SceneStatus | None = None
    review_notes: str | None = None


@api.patch("/scenes/{scene_id}")
def review_scene(scene_id: int, body: SceneReview, s: Session = SessionDep):
    sc = _get(s, Scene, scene_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(sc, k, v)
    s.add(sc)
    s.commit()
    s.refresh(sc)
    return sc


class SceneFix(BaseModel):
    shot_patch: dict[str, Any] = {}


@api.post("/scenes/{scene_id}/regenerate", status_code=202)
def regenerate_scene(scene_id: int, body: SceneFix, s: Session = SessionDep):
    sc = _get(s, Scene, scene_id)
    if {"start", "end"} & set(body.shot_patch):
        raise HTTPException(422, "a correção de cena não altera o tempo (start/end)")
    return {"job_id": get_queue().submit("regenerate_scene", body.model_dump(),
                                         episode_id=sc.episode_id, scene_id=scene_id)}


@api.get("/scenes/{scene_id}/preview")
def scene_preview(scene_id: int, s: Session = SessionDep):
    sc = _get(s, Scene, scene_id)
    if not sc.preview_path or not Path(sc.preview_path).is_file():
        raise HTTPException(404, "prévia ainda não gerada")
    return FileResponse(sc.preview_path, media_type="image/png")


class ExportIn(BaseModel):
    allow_unapproved: bool = False


@api.post("/episodes/{episode_id}/export", status_code=202)
def export_episode(episode_id: int, body: ExportIn, s: Session = SessionDep):
    _get(s, Episode, episode_id)
    return {"job_id": get_queue().submit("export_episode", body.model_dump(), episode_id=episode_id)}


@api.get("/episodes/{episode_id}/video")
def episode_video(episode_id: int, s: Session = SessionDep):
    ep = _get(s, Episode, episode_id)
    if not ep.output_path or not Path(ep.output_path).is_file():
        raise HTTPException(404, "vídeo ainda não exportado")
    return FileResponse(ep.output_path, media_type="video/mp4")


# --- tarefas, assets e auditoria ----------------------------------------------------
@api.get("/jobs")
def list_jobs(episode_id: int | None = None, limit: int = 50, s: Session = SessionDep):
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if episode_id is not None:
        q = q.where(Job.episode_id == episode_id)
    return s.exec(q).all()


@api.get("/jobs/{job_id}")
def get_job(job_id: int, s: Session = SessionDep):
    return _get(s, Job, job_id)


@api.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    try:
        return get_queue().cancel(job_id)
    except KeyError as e:
        raise HTTPException(404, "tarefa não encontrada") from e


@api.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: int):
    try:
        return {"job_id": get_queue().retry(job_id)}
    except KeyError as e:
        raise HTTPException(404, "tarefa não encontrada") from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@api.get("/assets")
def list_assets(series_id: int | None = None, kind: str | None = None, s: Session = SessionDep):
    q = select(Asset).order_by(Asset.id.desc())
    if series_id is not None:
        q = q.where(Asset.series_id == series_id)
    if kind:
        q = q.where(Asset.kind == kind)
    return s.exec(q).all()


@api.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, s: Session = SessionDep):
    a = _get(s, Asset, asset_id)
    path = Path(a.path).resolve()
    if not path.is_relative_to(get_settings().data_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "arquivo indisponível")
    return FileResponse(path)


@api.get("/generations")
def list_generations(job_id: int | None = None, s: Session = SessionDep):
    q = select(GenerationRecord).order_by(GenerationRecord.id.desc()).limit(200)
    if job_id is not None:
        q = q.where(GenerationRecord.job_id == job_id)
    return s.exec(q).all()


# --- ComfyUI ------------------------------------------------------------------------
class InstanceIn(BaseModel):
    name: str = Field(pattern=SLUG)
    url: str


@api.get("/comfyui/instances")
def comfy_instances(refresh: bool = True):
    reg = get_registry()
    if refresh:
        reg.refresh_all()
    return [i.public() for i in reg.all()]


@api.post("/comfyui/instances", status_code=201)
def comfy_add_instance(body: InstanceIn):
    try:
        inst = get_registry().add(body.name, body.url, declared=True)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return get_registry().refresh(inst).public()


@api.post("/comfyui/discover")
def comfy_discover():
    reg = get_registry()
    found = reg.discover(get_settings().comfyui_scan_ports)
    return [reg.refresh(i).public() for i in found]


def _instance(name: str):
    try:
        return get_registry().get(name)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@api.get("/comfyui/instances/{name}/models")
def comfy_models(name: str, refresh: bool = False):
    try:
        return get_registry().models(_instance(name), force=refresh)
    except ComfyError as e:
        raise HTTPException(502, str(e)) from e


@api.get("/comfyui/instances/{name}/nodes")
def comfy_nodes(name: str, refresh: bool = False):
    try:
        info = get_registry().object_info(_instance(name), force=refresh)
    except ComfyError as e:
        raise HTTPException(502, str(e)) from e
    return sorted(info)


@api.get("/comfyui/workflows")
def comfy_workflows():
    return [{"name": w.name, "kind": w.kind, "description": w.meta.get("description", ""),
             "placeholders": sorted(w.placeholders()), "nodes": sorted(w.node_classes())}
            for w in list_workflows(get_settings().workflows_dir)]


@api.get("/comfyui/workflows/{workflow}/check")
def comfy_check(workflow: str, instance: str | None = None):
    """Relatório de dependências: nós e modelos ausentes por instância, com orientação de instalação."""
    if not re.match(SLUG, workflow):
        raise HTTPException(422, "nome de workflow inválido")
    path = get_settings().workflows_dir / f"{workflow}.json"
    if not path.is_file():
        raise HTTPException(404, "workflow não encontrado")
    try:
        wf = load(path)
    except WorkflowError as e:
        raise HTTPException(422, str(e)) from e
    reg = get_registry()
    targets = [_instance(instance)] if instance else reg.all()
    reports = []
    for inst in targets:
        reg.refresh(inst)
        if not inst.online:
            reports.append({"instance": inst.name, "compatible": False, "error": inst.last_error})
            continue
        try:
            reports.append(reg.compatibility(inst, wf))
        except ComfyError as e:
            reports.append({"instance": inst.name, "compatible": False, "error": str(e)})
    return reports


app.include_router(api)
