"""Imagens-chave por IA para o modo "imagens" do motor-lofi.

Cada cena vira um fundo (`fundo_<cenário>`) mais recortes dos personagens
(`<personagem>_<vista>_<emoção>_<pose>`), no padrão de nomes de lofi/imagens.py.
O motor anima a composição: câmera, paralaxe, respiração, piscar e quem fala se mexendo.

As imagens ficam em data/series/<slug>/imagens/ e são reaproveitadas entre episódios:
uma pose gerada uma vez serve para a série inteira, o que mantém o personagem igual
e economiza GPU.
"""
import re
from pathlib import Path

from PIL import Image

# vocabulário do motor (rig) -> nomes do lofi/imagens.py
VIEW_PT = {"front": "frente", "3/4": "34", "three_quarter": "34", "side": "lado", "back": "costas"}
EMOTION_PT = {"neutral": "neutro", "fear": "medo", "shock": "choque", "angry": "raiva", "sad": "triste",
              "determined": "determinado", "happy": "feliz", "suspicious": "desconfiado", "crying": "choro",
              "tired": "cansado"}
# nomes em português -> chaves do style_master
VIEW_EN = {"frente": "front", "34": "three_quarter", "lado": "side", "costas": "back"}
EMOTION_EN = {v: k for k, v in EMOTION_PT.items()}
POSE_PROMPT = {"em_pe": "standing, full body", "andando": "walking, full body, mid-stride",
               "correndo": "running, full body in motion", "agachado": "crouching and hiding, full body",
               "sentado": "sitting, full body", "apontando": "pointing with the arm extended, full body",
               "segurando": "holding an object in the hands, full body",
               "celular": "talking on a smartphone, full body"}
POSE_FROM_RIG = {"run": "correndo", "walk": "andando", "hide": "agachado", "sit": "sentado",
                 "point": "apontando", "hold": "segurando", "phone": "celular"}
POSITIONS = {1: ["centro"], 2: ["esq", "dir"], 3: ["esq", "centro", "dir"]}
IMAGES_DIR = "imagens"
NAME_RE = re.compile(r"[^a-z0-9_]+")


def _clean(text: str) -> str:
    return NAME_RE.sub("_", text.lower()).strip("_")


def actor_pose(actor: dict) -> tuple[str, str, str]:
    view = VIEW_PT.get(actor.get("view", "front"), "frente")
    emotion = EMOTION_PT.get(actor.get("emotion", "neutral"), "neutro")
    if actor.get("run"):
        pose = "correndo"
    elif actor.get("walk"):
        pose = "andando"
    else:
        pose = POSE_FROM_RIG.get(actor.get("pose", "idle"), "em_pe")
    return view, emotion, pose


def character_image_name(char: str, view: str, emotion: str, pose: str) -> str:
    return _clean(f"{char}_{view}_{emotion}_{pose}")


def background_name(environment: str) -> str:
    return "fundo_" + _clean(environment)


def scene_images(shot: dict, environment: str | None) -> dict:
    """Configuração `imagens` do lofi/imagens.quadro para um plano do motor."""
    actors = shot.get("actors", [])[:3]
    positions = POSITIONS.get(len(actors), ["centro"] * len(actors))
    people = []
    for actor, pos in zip(actors, positions):
        view, emotion, pose = actor_pose(actor)
        person = {"img": character_image_name(actor["char"], view, emotion, pose), "pos": pos,
                  "fala": actor["char"]}
        if pose in ("andando", "correndo"):
            person["andar"] = ["esq", "dir"] if pos != "dir" else ["centro", "dir"]
        people.append(person)
    zoom = (shot.get("camera") or {}).get("zoom", 1.0)
    zoom = zoom[-1] if isinstance(zoom, list) else zoom
    cfg = {"personagens": people, "plano": "close" if zoom >= 1.25 else "medio" if zoom >= 1.1 else "aberto"}
    if environment:
        cfg["fundo"] = background_name(environment)
    return cfg


def requirements(cfgs: list[dict]) -> dict[str, dict]:
    """Nome da imagem -> o que gerar. Personagem: {char, view, emotion, pose}; fundo: {environment}."""
    needed: dict[str, dict] = {}
    for cfg in cfgs:
        if cfg.get("fundo"):
            needed.setdefault(cfg["fundo"], {"kind": "environment", "environment": cfg["fundo"][len("fundo_"):]})
        for person in cfg.get("personagens", []):
            char = person["fala"]
            rest = person["img"][len(_clean(char)) + 1:].split("_", 2)
            if len(rest) == 3:
                view, emotion, pose = rest
                needed.setdefault(person["img"], {"kind": "character", "char": char, "view": view,
                                                  "emotion": emotion, "pose": pose})
    return needed


def anchor_for_ipadapter(src: Path, dest: Path) -> Path:
    """Prepara a imagem âncora para o IPAdapter (que recorta o centro em quadrado).

    Folha de referência deitada (frente, 3/4 e costas lado a lado): usa o terço da esquerda,
    que é a vista de frente. Depois completa com cinza até ficar quadrada, sem cortar a cabeça nem os pés.
    """
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w > h * 1.2:
        im = im.crop((0, 0, w // 3, h))
        w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (200, 200, 202))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2))
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.resize((1024, 1024), Image.LANCZOS).save(dest)
    return dest
