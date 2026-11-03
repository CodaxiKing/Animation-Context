"""Produção do episódio: tarefas da fila que ligam o banco, o motor-lofi e o ComfyUI.

Pasta de trabalho de cada série, no mesmo formato de series/<serie>/ do motor (lida pelo lofi.render):
  data/series/<slug>/personagens.json      fichas travadas (o motor sempre usa estas)
  data/series/<slug>/epNN.json             roteiro técnico do episódio
  data/series/<slug>/imagens/              imagens-chave (fundos e recortes) compartilhadas pela série
  data/series/<slug>/epNN/scenes/          prévias de cada cena
"""
import json
import re
import shutil
from pathlib import Path

from sqlmodel import Session, select

from . import keyframes, motor, story_analysis, style_master
from .comfyui import workflow as wfmod
from .comfyui.orchestrator import Cancelled, run_workflow
from .comfyui.registry import NoCompatibleInstance
from .config import get_settings
from .db import session_scope
from .jobs import JobCancelled, JobContext, task
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
def plan_episode(s: Session, episode: Episode, *, duration: float | None = None) -> list[Scene]:
    """Roteiro -> beats (quem fala, quem está em cena, onde) -> planos do motor -> cenas revisáveis."""
    series = s.get(Series, episode.series_id)
    chars = s.exec(select(Character).where(Character.series_id == series.id)).all()
    envs = s.exec(select(Environment).where(Environment.series_id == series.id)).all()
    preset = style_master.get(series.style_preset)
    env_by_slug = {e.slug: e for e in envs}

    beats = story_analysis.analyze(
        episode.script, [{"slug": c.slug, "name": c.name} for c in chars],
        [{"slug": e.slug, "name": e.name} for e in envs])
    if not beats:
        raise ValueError("roteiro sem linhas de ação ou fala")
    first_env = next((env_by_slug[b.environment] for b in beats
                      if b.environment and env_by_slug[b.environment].motor_scene), None)
    first_env = first_env or next((e for e in envs if e.motor_scene), None)
    spec = motor.lofi("autodirect").compile_story(
        story_analysis.compiler_text(beats), title=series.title, episode=episode.number,
        actor=chars[0].slug if chars else "roberta",
        scene=first_env.motor_scene if first_env else "apartamento",
        visual_profile=preset.motor_profile, duration=duration)
    if len(spec["shots"]) != len(beats):
        raise RuntimeError(f"compilador gerou {len(spec['shots'])} planos para {len(beats)} linhas")
    spec["episode_title"] = episode.title or spec["episode_title"]

    for old in s.exec(select(Scene).where(Scene.episode_id == episode.id)).all():
        s.delete(old)
    scenes = []
    for i, (shot, beat) in enumerate(zip(spec["shots"], beats)):
        env = env_by_slug.get(beat.environment) if beat.environment else None
        if env and env.motor_scene:
            shot["scene"] = env.motor_scene
        if chars:
            story_analysis.cast_shot(shot, beat)
        scene = Scene(episode_id=episode.id, index=i, start=shot["start"], end=shot["end"], text=beat.text,
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
    path = series_dir(series) / f"ep{episode.number:02d}.json"
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
               series_id: int, tags: list[str], *, images: dict[str, Path] | None = None,
               on_progress=None) -> list[Asset]:
    settings = get_settings()
    wf = wfmod.load(settings.workflows_dir / f"{workflow_name}.json")
    result = run_workflow(
        wf, params, out_dir, images=images, seed=ctx.params.get("seed"), instance=ctx.params.get("instance"),
        job_id=ctx.job_id, max_retries=settings.comfyui_max_retries, timeout=settings.comfyui_timeout_s,
        on_progress=on_progress or (lambda p: ctx.progress(0.1 + 0.85 * (p.value / p.max if p.max else 0),
                                                            f"ComfyUI {p.phase}")),
        should_cancel=ctx.should_cancel, use_websocket=settings.comfyui_websocket)
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


# --- imagens-chave (ComfyUI) para o modo "imagens" do motor ----------------------------
def images_dir(series: Series) -> Path:
    path = series_dir(series) / keyframes.IMAGES_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def keyframe_plan(s: Session, episode: Episode) -> dict:
    series = s.get(Series, episode.series_id)
    folder = images_dir(series)
    scenes = s.exec(select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.index)).all()
    cfgs = [keyframes.scene_images(sc.shot, sc.environment) for sc in scenes]
    needed = keyframes.requirements(cfgs)
    for name, req in needed.items():
        req["exists"] = (folder / f"{name}.png").is_file()
    return {"scenes": [{"scene_id": sc.id, "index": sc.index, "imagens": cfg} for sc, cfg in zip(scenes, cfgs)],
            "images": needed, "missing": sorted(n for n, r in needed.items() if not r["exists"])}


def _anchor_path(s: Session, series: Series, char: Character) -> Path:
    if not char.reference_assets:
        raise ValueError(f"{char.slug}: sem imagem âncora. Gere a folha de referência ou envie uma imagem.")
    asset = s.get(Asset, char.reference_assets[0])
    if asset is None or not Path(asset.path).is_file():
        raise ValueError(f"{char.slug}: arquivo da imagem âncora não encontrado")
    dest = series_dir(series) / "assets" / "anchors" / f"{char.slug}_{asset.id}.png"
    return dest if dest.is_file() else keyframes.anchor_for_ipadapter(Path(asset.path), dest)


def _import_keyframe(series: Series, raw: Path, name: str, job_id: int) -> Path:
    out = Path(motor.lofi("imagens").importar(str(series_dir(series)), str(raw), name))
    with session_scope() as s:
        s.add(Asset(series_id=series.id, kind="keyframe", path=str(out), tags=[name], job_id=job_id,
                    meta={"source": str(raw)}))
        s.commit()
    return out


@task("generate_keyframes")
def generate_keyframes_task(ctx: JobContext) -> dict:
    """Gera fundos e poses que faltam (ComfyUI), recorta, e liga as cenas ao modo "imagens" do motor."""
    with session_scope() as s:
        ep = s.get(Episode, ctx.episode_id)
        series = s.get(Series, ep.series_id)
        preset = style_master.get(series.style_preset)
        plan = keyframe_plan(s, ep)
        chars = {c.slug: c for c in s.exec(select(Character).where(Character.series_id == series.id))}
        envs = {e.slug: e for e in s.exec(select(Environment).where(Environment.series_id == series.id))}
        s.expunge_all()

    missing = plan["missing"] if not ctx.params.get("only") else [n for n in plan["missing"] if n in ctx.params["only"]]
    generated, errors = [], {}
    raw_dir = series_dir(series) / "assets" / "raw"
    for i, name in enumerate(missing):
        req = plan["images"][name]
        base = i / max(1, len(missing))
        def progress(p, base=base, name=name):
            frac = p.value / p.max if p.max else 0
            ctx.progress(0.05 + 0.8 * (base + frac / max(1, len(missing))), f"{name}: ComfyUI {p.phase}")
        ctx.progress(0.05 + 0.8 * base, f"gerando {name} ({i + 1}/{len(missing)})")
        try:
            if req["kind"] == "environment":
                env = envs.get(req["environment"])
                desc = (env.description or env.name) if env else req["environment"].replace("_", " ")
                prompts = style_master.environment_prompt(preset, desc,
                                                          time_of_day=ctx.params.get("time_of_day", "night"))
                assets = _run_comfy(ctx, ctx.params.get("environment_workflow", "environment"), prompts,
                                    raw_dir, "environment", series.id, [name], on_progress=progress)
            else:
                char = chars.get(req["char"])
                if char is None:
                    raise ValueError(f"personagem {req['char']} não cadastrado na série")
                with session_scope() as s:
                    anchor = _anchor_path(s, series, char)
                prompts = style_master.character_prompt(
                    preset, char.description or char.name, view=keyframes.VIEW_EN[req["view"]],
                    emotion=keyframes.EMOTION_EN[req["emotion"]], pose=keyframes.POSE_PROMPT[req["pose"]],
                    background="plain flat light-gray background, no props, no floor texture")
                assets = _run_comfy(ctx, ctx.params.get("character_workflow", "character_pose"), prompts,
                                    raw_dir, "character_pose", series.id, [name, char.slug],
                                    images={"reference": anchor}, on_progress=progress)
            _import_keyframe(series, Path(assets[0].path), name, ctx.job_id)
            generated.append(name)
        except (JobCancelled, Cancelled, NoCompatibleInstance):
            raise
        except Exception as e:  # uma imagem com problema não derruba as outras
            errors[name] = f"{type(e).__name__}: {e}"

    ctx.progress(0.88, "ligando cenas às imagens")
    folder = images_dir(series)
    ready, blocked = [], {}
    with session_scope() as s:
        ep = s.get(Episode, ctx.episode_id)
        for item in plan["scenes"]:
            sc = s.get(Scene, item["scene_id"])
            cfg = item["imagens"]
            names = [p["img"] for p in cfg["personagens"]] + ([cfg["fundo"]] if cfg.get("fundo") else [])
            absent = [n for n in names if not (folder / f"{n}.png").is_file()]
            if absent:
                sc.status, sc.review_notes = SceneStatus.needs_fix, "imagens faltando: " + ", ".join(absent)
                blocked[sc.index] = absent
            else:
                sc.shot = {**sc.shot, "imagens": cfg}
                sc.version += 1
                ready.append(sc)
            s.add(sc)
        s.commit()
        if ready:
            render_previews(s, ep, ready)
        ready_idx = [sc.index for sc in ready]
    return {"generated": generated, "errors": errors, "scenes_ready": ready_idx,
            "scenes_blocked": blocked}
