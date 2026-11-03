"""Coloca clipes de vídeo gerados (Wan 2.2) por cima do episódio renderizado pelo motor.

Cada clipe entra exatamente no intervalo da cena: é redimensionado e cortado para o quadro do
episódio, convertido para o mesmo fps e, se for mais curto que a cena, segura o último quadro.
O áudio do episódio (vozes, efeitos, trilha) é mantido como está.
"""
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import motor


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


def probe(path: Path) -> VideoInfo:
    """Lê dimensões, fps e duração pela saída do `ffmpeg -i` (não exige ffprobe)."""
    exe = motor.ffmpeg_exe()
    if not exe:
        raise RuntimeError("FFmpeg não encontrado")
    err = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", err)
    dur = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not (size and dur):
        raise RuntimeError(f"não consegui ler o vídeo {path.name}")
    h, m, s = dur.groups()
    return VideoInfo(int(size.group(1)), int(size.group(2)), float(fps.group(1)) if fps else 30.0,
                     int(h) * 3600 + int(m) * 60 + float(s), "Audio:" in err)


def splice(base: Path, clips: list[tuple[Path, float, float]], out: Path) -> Path:
    """clips: [(arquivo, início, fim)] em segundos do episódio."""
    if not clips:
        return base
    info = probe(base)
    w, h, fps = info.width, info.height, info.fps
    parts, prev = [], "0:v"
    for i, (_, start, end) in enumerate(clips, 1):
        dur = max(0.04, end - start)
        parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},"
            f"tpad=stop_mode=clone:stop_duration={dur:.3f},trim=duration={dur:.3f},"
            f"setpts=PTS-STARTPTS+{start:.3f}/TB[c{i}]")
        parts.append(f"[{prev}][c{i}]overlay=enable='between(t,{start:.3f},{end - 0.001:.3f})':eof_action=pass[v{i}]")
        prev = f"v{i}"
    cmd = [motor.ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(base)]
    for path, _, _ in clips:
        cmd += ["-i", str(path)]
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{prev}]"]
    if info.has_audio:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError("FFmpeg falhou ao montar os clipes: " + res.stderr[-1500:])
    return out
