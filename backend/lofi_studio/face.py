"""Cabeça redonda e máscaras de olhos e boca para as variantes por inpainting.

O estilo dark_cinematic_lofi_2d tem cabeça perfeitamente redonda, clara e com contorno escuro,
o que torna a detecção por círculo (Hough) confiável sem modelo de landmarks. As máscaras ficam
dentro da cabeça (nunca encostam no contorno), então a silhueta e o recorte não mudam.
"""
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

INSIDE = 0.86   # fração do raio que a máscara pode ocupar


@dataclass(frozen=True)
class Head:
    cx: float
    cy: float
    r: float


def _figure_bbox(rgb: np.ndarray, tol: int = 38) -> tuple[int, int, int, int] | None:
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.abs(rgb.astype(int) - bg).max(axis=2) > tol
    ys, xs = np.nonzero(diff)
    if not len(xs):
        return None
    return xs.min(), ys.min(), xs.max(), ys.max()


def find_head(path: Path) -> Head | None:
    """Maior círculo claro na parte de cima da figura. None se não encontrar com segurança."""
    rgb = np.asarray(Image.open(path).convert("RGB"))
    box = _figure_bbox(rgb)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    fig_h, fig_w = y1 - y0 + 1, x1 - x0 + 1
    if fig_h < 40:
        return None
    gray = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (5, 5), 1.2)
    r_min, r_max = max(6, int(fig_h / 24)), max(8, int(min(fig_h / 5, fig_w * 0.75)))
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1.2, minDist=r_min * 2, param1=110, param2=28,
                               minRadius=r_min, maxRadius=r_max)
    if circles is None:
        return None
    best, best_score = None, -1.0
    for cx, cy, r in circles[0]:
        if not (y0 - r * 0.3 <= cy <= y0 + fig_h * 0.45 and x0 <= cx <= x1):
            continue
        yy, xx = np.ogrid[:rgb.shape[0], :rgb.shape[1]]
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= (r * 0.7) ** 2
        brightness = float(gray[inside].mean()) if inside.any() else 0
        score = brightness * r
        if brightness > 140 and score > best_score:
            best, best_score = Head(float(cx), float(cy), float(r)), score
    return best


def _clip_to_head(mask: Image.Image, head: Head) -> Image.Image:
    circle = Image.new("L", mask.size, 0)
    r = head.r * INSIDE
    ImageDraw.Draw(circle).ellipse([head.cx - r, head.cy - r, head.cx + r, head.cy + r], fill=255)
    return Image.fromarray(np.minimum(np.asarray(mask), np.asarray(circle)))


def face_mask(size: tuple[int, int], head: Head, part: str, view: str = "frente") -> Image.Image:
    """Máscara branca (vermelho = 255) da região dos olhos ("eyes") ou da boca ("mouth").

    Em 3/4 e de lado os traços ficam deslocados para a direita (as poses são geradas viradas para a direita).
    """
    shift = {"frente": 0.0, "34": 0.18, "lado": 0.38}.get(view, 0.0) * head.r
    cx, cy, r = head.cx + shift, head.cy, head.r
    if part == "eyes":
        box = [cx - 0.62 * r, cy - 0.38 * r, cx + 0.62 * r, cy + 0.28 * r]
    elif part == "mouth":
        box = [cx - 0.42 * r, cy + 0.22 * r, cx + 0.42 * r, cy + 0.72 * r]
    else:
        raise ValueError(part)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    return _clip_to_head(mask, head)


def save_mask(src: Path, head: Head, part: str, view: str, dest: Path) -> Path:
    size = Image.open(src).size
    m = face_mask(size, head, part, view)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.merge("RGB", (m, m, m)).save(dest)
    return dest
