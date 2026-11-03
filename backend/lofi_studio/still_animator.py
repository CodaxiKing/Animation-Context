"""Anima uma ilustração pronta (um quadro só) sem separar camadas e sem GPU.

Movimento que a cena já sugere, aplicado sobre a própria imagem:
  câmera lenta com leve câmera na mão, respiração do personagem, piscar (olhos apagados por
  inpainting e pálpebra desenhada), lanterna tremulando com cone de luz, relâmpago no céu,
  chuva em duas camadas, ondulação na poça, granulação; som de chuva e trovão do motor-lofi.

    python -m lofi_studio.still_animator imagem.png cena.json saida.mp4

Os quadros vão direto para o FFmpeg (stdin), sem gravar imagens no disco.
"""
import json
import math
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


@dataclass
class Eye:
    box: tuple[int, int, int, int]          # região do olho (x0, y0, x1, y1), abaixo da sobrancelha
    lid: tuple[int, int, int, int]          # pálpebra fechada: linha (x0, y0, x1, y1)


@dataclass
class Light:
    lens: tuple[int, int]                   # centro da lanterna
    toward: tuple[int, int]                 # para onde o feixe aponta
    half_angle: float = 13.0                # graus
    reach: float = 380.0                    # px
    color: tuple[int, int, int] = (255, 196, 128)
    strength: float = 0.32


@dataclass
class Scene:
    duration: float = 8.0
    fps: int = 24
    zoom: float = 0.05                      # aproximação total da câmera
    focus: tuple[int, int] | None = None    # para onde a câmera se aproxima
    handheld: float = 2.2                   # px de balanço
    eyes: list[Eye] = field(default_factory=list)
    blinks: list[float] = field(default_factory=lambda: [1.9, 5.6])
    breath_box: tuple[int, int, int, int] | None = None   # corpo: (x0, y_ombro, x1, y_base)
    breath_px: float = 2.0
    lights: list[Light] = field(default_factory=list)
    sky_box: tuple[int, int, int, int] | None = None
    lightning: list[float] = field(default_factory=lambda: [3.2])
    puddle_box: tuple[int, int, int, int] | None = None
    rain: float = 1.0                       # 0 desliga
    rain_angle: float = -4.0                # graus (negativo: cai para a esquerda)
    grain: float = 3.0
    audio: str | None = "chuva_fora"        # ambiência do motor-lofi; None = mudo

    @classmethod
    def load(cls, path: Path) -> "Scene":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["eyes"] = [Eye(**e) for e in data.get("eyes", [])]
        data["lights"] = [Light(**lt) for lt in data.get("lights", [])]
        return cls(**data)


def _soft_box(h, w, box, feather):
    x0, y0, x1, y1 = box
    m = np.zeros((h, w), np.float32)
    m[max(0, y0):y1, max(0, x0):x1] = 1
    return cv2.GaussianBlur(m, (0, 0), feather) if feather else m


def _closed_eyes(img: np.ndarray, eyes: list[Eye]) -> np.ndarray:
    """Apaga o preto dos olhos com a cor da pele em volta e desenha a pálpebra fechada.

    O olho encosta na sobrancelha neste estilo: inpainting comum puxaria o preto dela para
    dentro do buraco. Por isso o preenchimento usa só pixels claros (pele) do entorno.
    """
    out = img.astype(np.float32)
    luma = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    for e in eyes:
        x0, y0, x1, y1 = e.box
        mask = np.zeros(img.shape[:2], np.uint8)
        mask[y0:y1, x0:x1] = (luma[y0:y1, x0:x1] < 90).astype(np.uint8) * 255
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))   # inclui a borda antisserrilhada do olho
        ring = cv2.dilate(mask, np.ones((25, 25), np.uint8)) & ~mask
        skin = (ring > 0) & (luma > 110)
        if not skin.any():
            continue
        # cor da pele com o gradiente vertical do rosto: média por linha, suavizada
        fill = out.copy()
        mean = img[skin].mean(axis=0)
        ys = np.nonzero(mask.any(axis=1))[0]
        for y in ys:
            row = skin[max(0, y - 6):y + 7]
            px = img[max(0, y - 6):y + 7][row]
            fill[y, mask[y] > 0] = px.mean(axis=0) if len(px) > 8 else mean
        soft = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 1.2)[..., None]
        fill = cv2.GaussianBlur(fill, (0, 0), 1.5)
        out = out * (1 - soft) + fill * soft
    out = np.clip(out, 0, 255).astype(np.uint8)
    for e in eyes:
        x0, y0, x1, y1 = e.lid   # pálpebra fechada: arco "︶"
        center = ((x0 + x1) // 2, (y0 + y1) // 2)
        cv2.ellipse(out, center, (max(2, (x1 - x0) // 2), 4), 0, 0, 180, (20, 17, 22), 4, cv2.LINE_AA)
    return out


def _cone(h, w, light: Light) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lx, ly = light.lens
    dx, dy = xx - lx, yy - ly
    dist = np.hypot(dx, dy) + 1e-3
    tx, ty = light.toward[0] - lx, light.toward[1] - ly
    tn = math.hypot(tx, ty)
    cos = (dx * tx + dy * ty) / (dist * tn)
    ang = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    beam = np.clip(1 - ang / light.half_angle, 0, 1) ** 1.6 * np.clip(1 - dist / light.reach, 0, 1) ** 1.3
    glow = np.exp(-(dist / 26) ** 2) * 1.6 + np.exp(-(dist / 70) ** 2) * 0.35
    return cv2.GaussianBlur(beam, (0, 0), 6) + glow


def _rain_layer(h, w, n, length, thick, angle, seed):
    rng = np.random.default_rng(seed)
    layer = np.zeros((h * 2, w), np.float32)
    dx = math.tan(math.radians(angle)) * length
    for _ in range(n):
        x, y = rng.integers(0, w), rng.integers(0, h * 2)
        ln = length * rng.uniform(0.6, 1.2)
        cv2.line(layer, (int(x), int(y)), (int(x + dx * ln / length), int(y + ln)), float(rng.uniform(0.4, 1)),
                 thick, cv2.LINE_AA)
    layer[h:] = np.maximum(layer[h:], layer[:h])   # emenda vertical
    layer[:h] = layer[h:]
    return cv2.GaussianBlur(layer, (0, 0), 0.6)


def _noise(t, *freqs):
    return sum(math.sin(t * f + i * 1.7) for i, f in enumerate(freqs)) / len(freqs)


def render(image: Path, scene: Scene, out: Path, ffmpeg: str) -> Path:
    src = np.asarray(Image.open(image).convert("RGB"))
    h, w = src.shape[:2]
    h, w = h - h % 2, w - w % 2
    src = src[:h, :w]
    base = src.astype(np.float32)
    blink = _closed_eyes(src, scene.eyes).astype(np.float32) if scene.eyes else base

    cones = [(_cone(h, w, lt)[..., None] * np.array(lt.color, np.float32) * lt.strength) for lt in scene.lights]
    sky = _soft_box(h, w, scene.sky_box, 40)[..., None] if scene.sky_box else None
    puddle = _soft_box(h, w, scene.puddle_box, 12) if scene.puddle_box else None
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    breath = None
    if scene.breath_box:
        x0, ys, x1, yb = scene.breath_box
        horiz = np.clip((x1 - xx) / 80, 0, 1) * np.clip((xx - x0 + 80) / 80, 0, 1)
        vert = np.clip((yb - yy) / max(1, yb - ys), 0, 1)
        breath = horiz * vert
    rain_far = _rain_layer(h, w, int(900 * scene.rain), 26, 1, scene.rain_angle, 1) if scene.rain else None
    rain_near = _rain_layer(h, w, int(260 * scene.rain), 58, 2, scene.rain_angle, 2) if scene.rain else None
    vignette = (1 - 0.35 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) ** 1.2)[..., None]
    rng = np.random.default_rng(7)
    fx, fy = scene.focus or (w / 2, h / 2)

    audio_path = _audio(scene, out.with_suffix(".wav")) if scene.audio else None
    cmd = [ffmpeg, "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(scene.fps), "-i", "-"]
    if audio_path:
        cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames = int(scene.duration * scene.fps)
    try:
        for i in range(frames):
            t = i / scene.fps
            u = t / scene.duration
            blinking = any(b <= t < b + 0.13 for b in scene.blinks)
            frame = (blink if blinking else base).copy()

            if breath is not None:      # respiração: ombros e cabeça sobem de leve
                dy = (scene.breath_px * math.sin(2 * math.pi * t / 3.8)) * breath
                frame = cv2.remap(frame, xx, yy + dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

            if puddle is not None:      # poça ondulando
                shift = (1.6 * np.sin(yy * 0.21 + t * 7.0) + 0.8 * np.sin(yy * 0.53 - t * 11.0)) * puddle
                frame = cv2.remap(frame, xx + shift, yy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

            for cone in cones:          # lanterna tremulando (de vez em quando falha)
                flick = 0.9 + 0.12 * _noise(t, 23.0, 37.0, 51.0)
                if 4.4 < t % 7.3 < 4.55:
                    flick *= 0.35
                frame += cone * flick

            flash = 0.0
            for lt in scene.lightning:  # relâmpago em dois pulsos
                for k, (d0, d1, a) in enumerate(((0, 0.06, 1.0), (0.12, 0.26, 0.7))):
                    if lt + d0 <= t < lt + d1:
                        flash = max(flash, a)
                    elif lt + d1 <= t < lt + d1 + 0.25 and k == 1:
                        flash = max(flash, a * (1 - (t - lt - d1) / 0.25))
            if flash:
                frame = frame * (1 + 0.18 * flash) + 22 * flash
                if sky is not None:
                    frame += sky * np.array([120, 110, 170], np.float32) * flash

            # câmera: aproximação lenta + câmera na mão
            z = 1 + scene.zoom * (0.5 - 0.5 * math.cos(math.pi * u))
            cx = w / 2 + (fx - w / 2) * u * 0.6
            cy = h / 2 + (fy - h / 2) * u * 0.6
            jx = scene.handheld * _noise(t, 1.3, 2.9, 0.7)
            jy = scene.handheld * 0.7 * _noise(t + 4, 1.7, 3.3, 0.9)
            m = np.float32([[z, 0, w / 2 - cx * z + jx], [0, z, h / 2 - cy * z + jy]])
            frame = cv2.warpAffine(frame, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

            if rain_far is not None:    # chuva por cima (não entra no zoom)
                for layer, speed, alpha in ((rain_far, 900, 55), (rain_near, 1500, 80)):
                    off = int(t * speed) % h
                    streak = layer[h - off:2 * h - off]
                    frame += streak[..., None] * np.array([alpha * 0.9, alpha * 0.95, alpha], np.float32)

            frame = frame * vignette + rng.normal(0, scene.grain, (h, w, 1)).astype(np.float32)
            proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        if audio_path:
            audio_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg falhou ao codificar o vídeo")
    return out


def _audio(scene: Scene, path: Path) -> Path:
    """Chuva e trovões sintetizados pelo motor-lofi (lofi.audio), sincronizados com os relâmpagos."""
    from . import motor
    audio = motor.lofi("audio")
    sr, d = audio.SR, scene.duration
    mix = audio.amb(scene.audio, d) * 1.6 + audio.drone(d, base=48.0, tense=0.35) * 0.5
    for lt in scene.lightning:
        th = audio.thunder()
        at = int((lt + 0.7) * sr)
        end = min(len(mix), at + len(th))
        if at < len(mix):
            mix[at:end] += th[:end - at] * 0.8
    mix = mix / max(1e-6, np.abs(mix).max()) * 0.8
    pcm = (mix * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(sr)
        wv.writeframes(pcm.tobytes())
    return path


def _ffmpeg() -> str:
    from . import motor
    exe = motor.ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg não encontrado: instale o FFmpeg ou `pip install imageio-ffmpeg`")
    return exe


if __name__ == "__main__":
    img, cfg, dst = sys.argv[1:4]
    print(render(Path(img), Scene.load(Path(cfg)), Path(dst), _ffmpeg()))
