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

from PIL import Image
from sqlmodel import Session, select

from . import compositor, face, keyframes, motor, story_analysis, style_master, voices
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
            series = s.get(Series, ep.series_id)
            clips = [(series_dir(series) / sc.shot["video_clip"], sc.start, sc.end)
                     for sc in sorted(scenes, key=lambda x: x.index) if sc.shot.get("video_clip")]
        ctx.progress(0.1, "renderizando vídeo")
        out = motor.render_video(spec_path)
        clips = [c for c in clips if c[0].is_file()]
        if clips:
            ctx.progress(0.9, f"aplicando {len(clips)} clipe(s) de vídeo")
            tmp = out.with_name(out.stem + "_clipes.mp4")
            compositor.splice(out, clips, tmp)
            tmp.replace(out)
        with session_scope() as s:
            ep = s.get(Episode, ctx.episode_id)
            ep.output_path, ep.status = str(out), EpisodeStatus.done
            s.add(ep)
            s.add(Asset(series_id=ep.series_id, kind="video", path=str(out), tags=[f"ep{ep.number:02d}"]))
            s.commit()
        return {"video": str(out), "video_clips": len(clips)}
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


def _pose_prompts(preset, char: Character, req: dict) -> dict:
    return style_master.character_prompt(
        preset, char.description or char.name, view=keyframes.VIEW_EN[req["view"]],
        emotion=keyframes.EMOTION_EN[req["emotion"]], pose=keyframes.POSE_PROMPT[req["pose"]],
        background="plain flat light-gray background, no props, no floor texture")


FACE_EDITS = {
    "_piscando": ("eyes", "EDIT: eyes closed, shut eyelids drawn as short curved black lines, same face",
                  ", open eyes, pupils, eyeballs"),
    "_falando": ("mouth", "EDIT: mouth open while speaking, small simple open mouth with a dark interior, same face",
                 ", closed mouth, teeth, tongue, lips"),
}


def _pose_source(series: Series, name: str) -> Path:
    """Imagem original (antes do recorte) de uma pose; para poses importadas, o recorte sobre cinza liso."""
    with session_scope() as s:
        asset = s.exec(select(Asset).where(Asset.series_id == series.id, Asset.kind == "keyframe")
                       .order_by(Asset.id.desc())).all()
        src = next((a.meta.get("source") for a in asset if name in a.tags and a.meta.get("source")), None)
    if src and Path(src).is_file():
        return Path(src)
    cut = Image.open(images_dir(series) / f"{name}.png").convert("RGBA")
    flat = Image.new("RGB", cut.size, (200, 200, 202))
    flat.paste(cut, mask=cut.split()[3])
    dest = series_dir(series) / "assets" / "raw" / f"{name}_flat.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    flat.save(dest)
    return dest


def face_variants(ctx: JobContext, series: Series, preset, char: Character, name: str, req: dict,
                  errors: dict, on_progress=None) -> list[str]:
    """Gera <pose>_piscando e <pose>_falando por inpainting só nos olhos / na boca."""
    if req["view"] == "costas":
        return []
    todo = [sfx for sfx in FACE_EDITS if not (images_dir(series) / f"{name}{sfx}.png").is_file()]
    if not todo:
        return []
    src = _pose_source(series, name)
    head = face.find_head(src)
    if head is None:
        for sfx in todo:
            errors[name + sfx] = "rosto não encontrado na pose (variante não gerada)"
        return []
    made = []
    base = _pose_prompts(preset, char, req)
    for sfx in todo:
        part, edit, neg = FACE_EDITS[sfx]
        try:
            mask = face.save_mask(src, head, part, req["view"],
                                  series_dir(series) / "assets" / "masks" / f"{name}{sfx}.png")
            prompts = {"prompt": base["prompt"] + "\n" + edit, "negative": base["negative"] + neg}
            assets = _run_comfy(ctx, ctx.params.get("face_workflow", "face_inpaint"), prompts,
                                series_dir(series) / "assets" / "raw", "face_variant", series.id,
                                [name + sfx, char.slug], images={"image": src, "mask": mask},
                                on_progress=on_progress)
            _import_keyframe(series, Path(assets[0].path), name + sfx, ctx.job_id)
            made.append(name + sfx)
        except (JobCancelled, Cancelled, NoCompatibleInstance):
            raise
        except Exception as e:
            errors[name + sfx] = f"{type(e).__name__}: {e}"
    return made


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
            ctx.progress(0.05 + 0.55 * (base + frac / max(1, len(missing))), f"{name}: ComfyUI {p.phase}")
        ctx.progress(0.05 + 0.55 * base, f"gerando {name} ({i + 1}/{len(missing)})")
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
                prompts = _pose_prompts(preset, char, req)
                assets = _run_comfy(ctx, ctx.params.get("character_workflow", "character_pose"), prompts,
                                    raw_dir, "character_pose", series.id, [name, char.slug],
                                    images={"reference": anchor}, on_progress=progress)
            _import_keyframe(series, Path(assets[0].path), name, ctx.job_id)
            generated.append(name)
        except (JobCancelled, Cancelled, NoCompatibleInstance):
            raise
        except Exception as e:  # uma imagem com problema não derruba as outras
            errors[name] = f"{type(e).__name__}: {e}"

    variants = []
    if ctx.params.get("face_variants", True):
        poses = [(n, r) for n, r in plan["images"].items() if r["kind"] == "character"
                 and (images_dir(series) / f"{n}.png").is_file()
                 and (not ctx.params.get("only") or n in ctx.params["only"])]
        for i, (name, req) in enumerate(poses):
            ctx.progress(0.6 + 0.25 * i / max(1, len(poses)), f"olhos e boca: {name}")
            if req["char"] in chars:
                variants += face_variants(ctx, series, preset, chars[req["char"]], name, req, errors)

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
    return {"generated": generated, "face_variants": variants, "errors": errors, "scenes_ready": ready_idx,
            "scenes_blocked": blocked}


# --- vozes (Chatterbox) e tempo das cenas pelo áudio -------------------------------------
def _free_comfyui_vram() -> None:
    from .comfyui.registry import LOCAL_HOSTS, get_registry
    from urllib.parse import urlparse
    reg = get_registry()
    for inst in reg.all():
        if urlparse(inst.url).hostname in LOCAL_HOSTS:
            try:
                with reg.client(inst) as c:
                    c.free()
            except Exception:
                pass  # ComfyUI fechado: nada a liberar


def voice_lines(s: Session, episode: Episode, *, seed: int | None = None) -> tuple[list[voices.Line], set[str]]:
    """Falas do episódio, uma por cena. Devolve (linhas, vozes sem amostra cadastrada)."""
    series = s.get(Series, episode.series_id)
    chars = {c.slug: c for c in s.exec(select(Character).where(Character.series_id == series.id))}
    scenes = s.exec(select(Scene).where(Scene.episode_id == episode.id).order_by(Scene.index)).all()
    lines_dir = episode_dir(series, episode) / "voices" / "lines"
    lines, missing = [], set()
    for sc in scenes:
        notes = sc.shot.get("director_notes", {})
        voice, text = notes.get("speaker"), notes.get("dialogue")
        if not voice and voices.NARRATOR in chars:
            voice, text = voices.NARRATOR, sc.text
        if not voice or not text:
            continue
        char = chars.get(voice)
        sample = s.get(Asset, char.voice.get("sample_asset")) if char and char.voice.get("sample_asset") else None
        if sample is None or not Path(sample.path).is_file():
            missing.add(voice)
            continue
        actor = next((a for a in sc.shot.get("actors", []) if a.get("char") == voice), {})
        emotion = actor.get("emotion", "neutral")
        exaggeration, cfg_weight, temperature = voices.voice_params(char.voice, emotion)
        lines.append(voices.Line(
            scene_id=sc.id, index=sc.index, voice=voice, text=voices.clean_text(text), emotion=emotion,
            reference=Path(sample.path), exaggeration=exaggeration, cfg_weight=cfg_weight, temperature=temperature,
            seed=seed if seed is not None else char.voice.get("seed"),
            out=lines_dir / f"{sc.index:03d}_{voice}.wav"))
    return lines, missing


@task("generate_voices")
def generate_voices_task(ctx: JobContext) -> dict:
    """Gera as falas (só as que mudaram), monta as trilhas por personagem e ajusta o tempo das cenas."""
    with session_scope() as s:
        ep = s.get(Episode, ctx.episode_id)
        series = s.get(Series, ep.series_id)
        lines, missing = voice_lines(s, ep, seed=ctx.params.get("seed"))
        ep_folder = episode_dir(series, ep)
        series_folder = series_dir(series)
    if not lines:
        raise ValueError("nenhuma fala com voz cadastrada" + (f"; faltam amostras de: {sorted(missing)}" if missing else ""))

    def fresh(ln):
        meta = ln.out.with_suffix(".json")
        return ln.out.is_file() and meta.is_file() and json.loads(meta.read_text())["key"] == ln.cache_key()

    todo = [ln for ln in lines if not fresh(ln)]
    errors = {}
    if todo:
        if get_settings().free_comfyui_vram_before_voice:
            ctx.progress(0.02, "liberando VRAM do ComfyUI")
            _free_comfyui_vram()

        def on_event(ev):
            if ev.get("event") == "loaded":
                ctx.progress(0.1, f"modelo de voz carregado ({ev.get('device')})")
            elif ev.get("event") in ("done", "error"):
                ctx.progress(0.1 + 0.75 * (ev["index"] + 1) / len(todo), f"fala {ev['index'] + 1}/{len(todo)}")

        try:
            results = voices.run_chatterbox(todo, on_event=on_event, should_cancel=ctx.should_cancel)
        except voices.VoiceCancelled:
            raise JobCancelled() from None
        for i, ln in enumerate(todo):
            ev = results.get(i, {"event": "error", "error": "sem resposta do worker"})
            if ev["event"] == "done" and ln.out.is_file():
                ln.out.with_suffix(".json").write_text(json.dumps({"key": ln.cache_key(), "text": ln.text,
                                                                   "emotion": ln.emotion, "seed": ln.seed}))
            else:
                errors[ln.index] = ev.get("error")

    ctx.progress(0.9, "montando trilhas e ajustando o tempo das cenas")
    usable = {ln.index: (ln.voice, ln.out) for ln in lines if ln.index not in errors and ln.out.is_file()}
    if not usable:
        raise voices.VoiceError(f"nenhuma fala gerada: {errors}")
    tracks, spans = voices.build_tracks(usable, ep_folder / "voices")

    with session_scope() as s:
        ep = s.get(Episode, ctx.episode_id)
        scenes = s.exec(select(Scene).where(Scene.episode_id == ep.id).order_by(Scene.index)).all()
        pos = {sc.index: i for i, sc in enumerate(scenes)}
        speech = {pos[idx]: b - a for idx, (a, b) in spans.items()}
        times = voices.retime([sc.end - sc.start for sc in scenes], speech)
        clips, subs = [], []
        for sc, (start, end) in zip(scenes, times):
            sc.start, sc.end = start, end
            sc.shot = {**sc.shot, "start": start, "end": end}
            s.add(sc)
            if sc.index in spans:
                voice = usable[sc.index][0]
                src = list(spans[sc.index])
                clip = {"voice": voice, "src": src, "at": round(start + voices.LEAD_S, 3)}
                if voice == voices.NARRATOR:
                    clip["acoustic"] = None   # narrador: voz de estúdio, sem eco do lugar
                clips.append(clip)
                text = next(ln.text for ln in lines if ln.index == sc.index)
                subs.append({"voice": voice, "src": src, "text": voices.subtitle_chunks(text)})
        ep.spec = {**ep.spec, "total": times[-1][1] if times else ep.spec.get("total", 0),
                   "voices": {v: p.relative_to(series_folder).as_posix() for v, p in tracks.items()},
                   "clips": clips, "subs": subs}
        for voice, path in tracks.items():
            s.add(Asset(series_id=ep.series_id, kind="voice_track", path=str(path),
                        tags=[f"ep{ep.number:02d}", voice], job_id=ctx.job_id))
        s.add(ep)
        s.commit()
        total = ep.spec["total"]
    return {"lines": len(lines), "generated": len(todo) - len(errors), "cached": len(lines) - len(todo),
            "errors": errors, "missing_samples": sorted(missing), "tracks": sorted(tracks), "total": total}


# --- clipes de vídeo (Wan 2.2 image-to-video) ---------------------------------------------
WAN_NEGATIVE = ("static image, frozen, still frame, blurry, low quality, jpeg artifacts, deformed, distorted face, "
                "extra limbs, extra fingers, bad hands, flicker, text, subtitles, watermark, logo, 3d render, "
                "photorealistic, bright saturated colors, morphing, melting")
MAX_CLIP_FRAMES = 121


def clip_length(seconds: float, fps: int = 24) -> int:
    """Quadros no formato do Wan (4n + 1), entre 17 e 121."""
    n = round(seconds * fps / 4)
    return max(17, min(MAX_CLIP_FRAMES, 4 * n + 1))


def clip_prompt(preset, scene: Scene, extra: str = "") -> str:
    notes = scene.shot.get("director_notes", {})
    motion = []
    if any(a.get("run") for a in scene.shot.get("actors", [])):
        motion.append("the character runs")
    elif any(a.get("walk") for a in scene.shot.get("actors", [])):
        motion.append("the character walks")
    if notes.get("speaker"):
        motion.append("the character speaks with small natural head movement")
    motion.append("subtle natural motion, breathing, hair and clothes moving slightly, slow cinematic camera")
    return (f"{preset.short_prompt}. {scene.text}. " + ", ".join(motion) + (f". {extra}" if extra else "")
            + ". Keep the exact same art style, character design and colors as the first frame.")


@task("generate_video_clips")
def generate_video_clips_task(ctx: JobContext) -> dict:
    """Anima a prévia de cada cena escolhida com o Wan 2.2 e liga o clipe à cena (entra no export)."""
    with session_scope() as s:
        ep = s.get(Episode, ctx.episode_id)
        series = s.get(Series, ep.series_id)
        preset = style_master.get(series.style_preset)
        wanted = set(ctx.params["scenes"])
        scenes = [sc for sc in s.exec(select(Scene).where(Scene.episode_id == ep.id).order_by(Scene.index))
                  if sc.index in wanted]
        missing_preview = [sc for sc in scenes if not sc.preview_path or not Path(sc.preview_path).is_file()]
        if missing_preview:
            render_previews(s, ep, missing_preview)
        jobs = [(sc.id, sc.index, Path(sc.preview_path), clip_prompt(preset, sc, ctx.params.get("prompt_extra", "")),
                 min(ctx.params.get("seconds") or (sc.end - sc.start), MAX_CLIP_FRAMES / 24)) for sc in scenes]
        clip_dir = episode_dir(series, ep) / "clips"
        series_folder = series_dir(series)
    made, errors = {}, {}
    for i, (scene_id, index, image, prompt, seconds) in enumerate(jobs):
        def progress(p, i=i):
            frac = p.value / p.max if p.max else 0
            ctx.progress(0.05 + 0.9 * (i + frac) / len(jobs), f"cena {index}: Wan 2.2 {p.phase}")
        ctx.progress(0.05 + 0.9 * i / len(jobs), f"cena {index} ({i + 1}/{len(jobs)})")
        try:
            assets = _run_comfy(ctx, ctx.params.get("workflow", "wan22_i2v"),
                                {"prompt": prompt, "negative": WAN_NEGATIVE, "length": clip_length(seconds)},
                                series_folder / "assets" / "raw", "video_clip", series.id,
                                [f"ep{ep.number:02d}", f"scene{index}"], images={"image": image}, on_progress=progress)
            video = next((Path(a.path) for a in assets if Path(a.path).suffix.lower() in (".mp4", ".webm", ".mov")),
                         None)
            if video is None:
                raise RuntimeError("o workflow não devolveu um arquivo de vídeo")
            clip_dir.mkdir(parents=True, exist_ok=True)
            with session_scope() as s:
                sc = s.get(Scene, scene_id)
                dest = clip_dir / f"scene{index:03d}_v{sc.version}{video.suffix}"
                shutil.copy2(video, dest)
                sc.shot = {**sc.shot, "video_clip": dest.relative_to(series_folder).as_posix()}
                s.add(sc)
                s.commit()
            made[index] = str(dest)
        except (JobCancelled, Cancelled, NoCompatibleInstance):
            raise
        except Exception as e:
            errors[index] = f"{type(e).__name__}: {e}"
    return {"clips": made, "errors": errors}
