"""Produção do episódio: tarefas da fila que ligam o banco, o motor-lofi e o ComfyUI.

Pasta de trabalho de cada série (lida pelo lofi.render):
  data/series/<slug>/personagens.json      fichas travadas (o motor sempre usa estas)
  data/series/<slug>/epNN/spec.json        roteiro técnico do episódio
  data/series/<slug>/epNN/scenes/          prévias de cada cena
"""
import json
import re
import shutil
from pathlib import Path

from sqlmodel import Session, select

from . import motor, style_master
from .comfyui import workflow as wfmod
from .comfyui.orchestrator import run_workflow
from .config import get_settings
from .db import session_scope
from .jobs import JobContext, task
from .models import (Asset, Character, Environment, Episode, EpisodeStatus, Scene, SceneStatus, Series)


# --- pastas ---------------------------------------------------------------------
def series_dir(series: Series) -> Path:
    path = get_settings().data_dir / "series" / series.slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def episode_dir(series: Series, episode: Episode) -> Path:
    path = series_dir(series) / f"ep{episode.number:02d}"
    (path / "scenes").mkdir(parents=True, exist_ok=True)
    return path


def write_locked_sheets(s: Session, series: Series) -> Path:
    chars = s.exec(select(Character).where(Character.series_id == series.id)).all()
    data = {"_leia": "Gerado pelo Motor Lo-Fi V5 a partir das fichas travadas do banco."}
    data.update({c.slug: c.rig for c in chars if c.rig})
    path = series_dir(series) / "personagens.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


# --- importação de séries do motor ------------------------------------------------
def import_motor_series(s: Session, slug: str) -> Series:
    """Cadastra uma série existente do motor-lofi (personagens e lugares) sem alterar o repositório dela."""
    data = motor.read_motor_series(slug)
    if s.exec(select(Series).where(Series.slug == slug)).first():
        raise ValueError(f"série já importada: {slug}")
    title_match = re.search(r"^#\s+(.+)$", data["bible"], re.M)
    series = Series(slug=slug, title=title_match.group(1).strip() if title_match else slug,
                    motor_slug=slug, synopsis=data["bible"][:2000])
    s.add(series)
    s.flush()
    for cslug, sheet in data["characters"].items():
        if cslug.startswith("_") or not isinstance(sheet, dict):
            continue
        s.add(Character(series_id=series.id, slug=cslug, name=cslug.replace("_", " ").title(),
                        description=sheet.get("aparencia", ""), rig=sheet, locked=True))
    scenes = set(motor.available_scenes())
    for place, info in (data["world"].get("lugares") or {}).items():
        for scene in info.get("cenarios", []):
            if s.exec(select(Environment).where(Environment.series_id == series.id,
                                                Environment.slug == scene)).first():
                continue
            s.add(Environment(series_id=series.id, slug=scene, name=scene.replace("_", " "),
                              description=f"{place}: {info.get('nota', '')}".strip(),
                              motor_scene=scene if scene in scenes else None))
    s.commit()
    s.refresh(series)
    return series


# --- planejamento -------------------------------------------------------------------
def _match_environment(text: str, envs: list[Environment]) -> Environment | None:
    low = text.lower()
    for env in envs:
        for term in {env.slug.replace("_", " "), env.name.lower()}:
            if term and re.search(r"\b" + re.escape(term) + r"\b", low):
                return env
    return None


def plan_episode(s: Session, episode: Episode, *, duration: float | None = None) -> list[Scene]:
    series = s.get(Series, episode.series_id)
    chars = s.exec(select(Character).where(Character.series_id == series.id)).all()
    envs = s.exec(select(Environment).where(Environment.series_id == series.id)).all()
    preset = style_master.get(series.style_preset)
    actor = chars[0].slug if chars else "roberta"
    start_env = next((e for e in envs if e.motor_scene), None)

    spec = motor.lofi("autodirect").compile_story(
        episode.script, title=series.title, episode=episode.number, actor=actor,
        scene=start_env.motor_scene if start_env else "apartamento",
        visual_profile=preset.motor_profile, duration=duration)
    spec["episode_title"] = episode.title or spec["episode_title"]

    for old in s.exec(select(Scene).where(Scene.episode_id == episode.id)).all():
        s.delete(old)
    scenes = []
    for i, shot in enumerate(spec["shots"]):
        text = shot.get("director_notes", {}).get("text", "")
        env = _match_environment(text, envs)
        if env and env.motor_scene:
            shot["scene"] = env.motor_scene
        scene = Scene(episode_id=episode.id, index=i, start=shot["start"], end=shot["end"], text=text,
                      environment=env.slug if env else shot["scene"], shot=shot)
        s.add(scene)
        scenes.append(scene)

    episode.spec = spec
    episode.quality = motor.lofi("studio").quality_report(spec, story=episode.script, capabilities={})
    s.add(episode)
    s.commit()
    return scenes


def write_spec(s: Session, episode: Episode) -> Path:
    """Grava o spec com os planos atuais das cenas (as correções de cena entram aqui)."""
    series = s.get(Series, episode.series_id)
    write_locked_sheets(s, series)
    scenes = s.exec(select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.index)).all()
    spec = dict(episode.spec)
    if scenes:
        spec["shots"] = [sc.shot for sc in scenes]
        spec["total"] = max(sc.end for sc in scenes)
    path = episode_dir(series, episode) / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def render_previews(s: Session, episode: Episode, scenes: list[Scene]) -> list[Scene]:
    series = s.get(Series, episode.series_id)
    spec_path = write_spec(s, episode)
    times = [round((sc.start + sc.end) / 2, 1) for sc in scenes]
    frames = motor.render_frames(spec_path, times)
    out_dir = episode_dir(series, episode) / "scenes"
    for sc, frame in zip(scenes, frames):
        dest = out_dir / f"scene{sc.index:03d}_v{sc.version}.png"
        shutil.move(str(frame), dest)
        sc.preview_path, sc.status = str(dest), SceneStatus.ready
        s.add(sc)
        s.add(Asset(series_id=series.id, kind="scene_preview", path=str(dest),
                    tags=[f"ep{episode.number:02d}", f"scene{sc.index}"], meta={"version": sc.version}))
    s.commit()
    return scenes


# --- tarefas da fila ----------------------------------------------------------------
def _set_episode_status(episode_id: int, status: EpisodeStatus) -> None:
    with session_scope() as s:
        ep = s.get(Episode, episode_id)
        ep.status = status
        s.add(ep)
        s.commit()


@task("generate_episode")
def generate_episode_task(ctx: JobContext) -> dict:
    """Roteiro → planos → cenas → prévias. O episódio fica em revisão; o vídeo sai no export."""
    try:
        _set_episode_status(ctx.episode_id, EpisodeStatus.planning)
        with session_scope() as s:
            ep = s.get(Episode, ctx.episode_id)
            ctx.progress(0.05, "analisando roteiro")
            scenes = plan_episode(s, ep, duration=ctx.params.get("duration"))
            ctx.progress(0.35, f"{len(scenes)} cenas planejadas; renderizando prévias")
            _set_episode_status(ctx.episode_id, EpisodeStatus.generating)
            render_previews(s, ep, scenes)
            ctx.progress(0.95, "prévias prontas")
            quality = ep.quality
        _set_episode_status(ctx.episode_id, EpisodeStatus.review)
        return {"scenes": len(scenes), "quality_ok": quality.get("ok"), "findings": quality.get("findings", [])}
    except Exception:
        _set_episode_status(ctx.episode_id, EpisodeStatus.failed)
        raise


@task("regenerate_scene")
def regenerate_scene_task(ctx: JobContext) -> dict:
    """Corrige uma cena: aplica alterações no plano (params['shot_patch']) e refaz a prévia."""
    with session_scope() as s:
        sc = s.get(Scene, ctx.scene_id)
        patch = ctx.params.get("shot_patch") or {}
        if patch:
            sc.shot = {**sc.shot, **patch}
        sc.version += 1
        sc.status = SceneStatus.generating
        s.add(sc)
        s.commit()
        ep = s.get(Episode, sc.episode_id)
        try:
            render_previews(s, ep, [sc])
        except Exception:
            sc.status = SceneStatus.failed
            s.add(sc)
            s.commit()
            raise
        return {"scene": sc.id, "version": sc.version, "preview": sc.preview_path}


@task("export_episode")
def export_episode_task(ctx: JobContext) -> dict:
    _set_episode_status(ctx.episode_id, EpisodeStatus.rendering)
    try:
        with session_scope() as s:
            ep = s.get(Episode, ctx.episode_id)
            scenes = s.exec(select(Scene).where(Scene.episode_id == ep.id)).all()
            pending = [sc.index for sc in scenes if sc.status != SceneStatus.approved]
            if pending and not ctx.params.get("allow_unapproved"):
                raise ValueError(f"cenas não aprovadas: {pending}")
            spec_path = write_spec(s, ep)
        ctx.progress(0.1, "renderizando vídeo")
        out = motor.render_video(spec_path)
        with session_scope() as s:
            ep = s.get(Episode, ctx.episode_id)
            ep.output_path, ep.status = str(out), EpisodeStatus.done
            s.add(ep)
            s.add(Asset(series_id=ep.series_id, kind="video", path=str(out), tags=[f"ep{ep.number:02d}"]))
            s.commit()
        return {"video": str(out)}
    except Exception:
        _set_episode_status(ctx.episode_id, EpisodeStatus.failed)
        raise


def _run_comfy(ctx: JobContext, workflow_name: str, params: dict, out_dir: Path, kind: str,
               series_id: int, tags: list[str]) -> list[Asset]:
    settings = get_settings()
    wf = wfmod.load(settings.workflows_dir / f"{workflow_name}.json")
    result = run_workflow(
        wf, params, out_dir, seed=ctx.params.get("seed"), instance=ctx.params.get("instance"),
        job_id=ctx.job_id, max_retries=settings.comfyui_max_retries, timeout=settings.comfyui_timeout_s,
        on_progress=lambda p: ctx.progress(0.1 + 0.85 * (p.value / p.max if p.max else 0), f"ComfyUI {p.phase}"),
        should_cancel=ctx.should_cancel)
    assets = []
    with session_scope() as s:
        for f in result.files:
            a = Asset(series_id=series_id, kind=kind, path=str(f), tags=tags, job_id=ctx.job_id,
                      meta={"seed": result.seed, "instance": result.instance, "workflow": wf.name,
                            "generation_record": result.record_id})
            s.add(a)
            assets.append(a)
        s.commit()
        for a in assets:
            s.refresh(a)
    return assets


@task("character_sheet")
def character_sheet_task(ctx: JobContext) -> dict:
    with session_scope() as s:
        ch = s.get(Character, ctx.params["character_id"])
        series = s.get(Series, ch.series_id)
        prompts = style_master.character_sheet_prompt(style_master.get(series.style_preset),
                                                      ch.description or ch.name)
        out_dir = series_dir(series) / "assets" / "characters" / ch.slug
        series_id, slug = series.id, ch.slug
    assets = _run_comfy(ctx, ctx.params.get("workflow", "character_sheet"), prompts, out_dir,
                        "character_sheet", series_id, [slug])
    with session_scope() as s:
        ch = s.get(Character, ctx.params["character_id"])
        ch.reference_assets = [*ch.reference_assets, *[a.id for a in assets]]
        s.add(ch)
        s.commit()
    return {"assets": [a.id for a in assets]}


@task("environment_art")
def environment_art_task(ctx: JobContext) -> dict:
    with session_scope() as s:
        env = s.get(Environment, ctx.params["environment_id"])
        series = s.get(Series, env.series_id)
        prompts = style_master.environment_prompt(
            style_master.get(series.style_preset), env.description or env.name,
            time_of_day=ctx.params.get("time_of_day", "night"), weather=ctx.params.get("weather", ""))
        out_dir = series_dir(series) / "assets" / "environments" / env.slug
        series_id, slug = series.id, env.slug
    assets = _run_comfy(ctx, ctx.params.get("workflow", "environment"), prompts, out_dir,
                        "environment", series_id, [slug])
    with session_scope() as s:
        env = s.get(Environment, ctx.params["environment_id"])
        env.reference_assets = [*env.reference_assets, *[a.id for a in assets]]
        s.add(env)
        s.commit()
    return {"assets": [a.id for a in assets]}
