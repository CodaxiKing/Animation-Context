"""Controle de qualidade visual das imagens-chave (sem modelo de IA, só medidas da imagem).

Para cada pose de personagem, compara com a âncora (a referência de identidade):
  - rosto: a cabeça redonda do estilo foi encontrada?
  - pele: o rosto está no tom branco-acinzentado do estilo (pouca saturação, claro)?
  - cores: o histograma de cor da roupa/cabelo (fora o rosto e o fundo) parece com o da âncora?
  - recorte: o fundo foi removido (a figura não ocupa a imagem inteira)?

São sinais para revisar, não um veredito: uma pose de costas, por exemplo, não tem rosto.
"""
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import face

COLOR_MIN = 0.35      # correlação mínima de histograma (cabelo/roupa) com a âncora
SKIN_SAT_MAX = 0.32   # rosto do estilo: pouco saturado
SKIN_VAL_MIN = 0.55   # e claro
CUT_MAX = 0.88        # figura ocupando mais que isso do quadro: fundo provavelmente não saiu


@dataclass
class Check:
    image: str
    ok: bool = True
    score: float | None = None
    flags: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return asdict(self)


def _rgba(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGBA"))


def _hist(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h = cv2.calcHist([hsv], [0, 1], mask.astype(np.uint8) * 255, [24, 8], [0, 180, 0, 256])
    return cv2.normalize(h, h).flatten()


def _figure_mask(img: np.ndarray) -> np.ndarray:
    if img[..., 3].min() < 250:                       # recorte com transparência
        return img[..., 3] > 128
    rgb = img[..., :3].astype(int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    return np.abs(rgb - bg).max(axis=2) > 38


def _without_head(mask: np.ndarray, head: face.Head | None) -> np.ndarray:
    if head is None:
        return mask
    yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
    return mask & ((xx - head.cx) ** 2 + (yy - head.cy) ** 2 > (head.r * 1.05) ** 2)


def _flat(path: Path, tmp: Path) -> Path:
    """Recorte transparente -> sobre cinza liso (para a detecção da cabeça)."""
    im = Image.open(path).convert("RGBA")
    flat = Image.new("RGB", im.size, (200, 200, 202))
    flat.paste(im, mask=im.split()[3])
    flat.save(tmp)
    return tmp


def check_pose(pose: Path, anchor: Path | None, view: str, work: Path) -> Check:
    c = Check(pose.name)
    img = _rgba(pose)
    mask = _figure_mask(img)
    coverage = float(mask.mean())
    if coverage > CUT_MAX:
        c.flags.append(f"fundo não removido (figura ocupa {coverage:.0%} da imagem)")

    head = face.find_head(_flat(pose, work / f"_qc_{pose.stem}.png"))
    if view != "costas":
        if head is None:
            c.flags.append("rosto redondo não encontrado")
        else:
            yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
            # dentro do círculo achado; só usa o alfa se houver (a pele clara se confunde com fundo cinza)
            inner = (xx - head.cx) ** 2 + (yy - head.cy) ** 2 < (head.r * 0.6) ** 2
            if img[..., 3].min() < 250:
                inner = inner & (img[..., 3] > 128)
            if inner.any():
                hsv = cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2HSV)[inner]
                sat, val = float(np.median(hsv[:, 1])) / 255, float(np.median(hsv[:, 2])) / 255
                if sat > SKIN_SAT_MAX or val < SKIN_VAL_MIN:
                    c.flags.append(f"pele fora do estilo (saturação {sat:.2f}, brilho {val:.2f})")

    if anchor is not None and anchor.is_file():
        a = _rgba(anchor)
        a_mask = _figure_mask(a)
        a_head = face.find_head(_flat(anchor, work / f"_qc_anchor_{anchor.stem}.png"))
        body, a_body = _without_head(mask, head), _without_head(a_mask, a_head)
        if body.sum() > 200 and a_body.sum() > 200:
            score = float(cv2.compareHist(_hist(img[..., :3], body), _hist(a[..., :3], a_body), cv2.HISTCMP_CORREL))
            c.score = round(score, 3)
            if score < COLOR_MIN:
                c.flags.append(f"cores de roupa/cabelo diferentes da âncora (semelhança {score:.2f})")
    for f in work.glob("_qc_*.png"):
        f.unlink(missing_ok=True)
    c.ok = not c.flags
    return c
