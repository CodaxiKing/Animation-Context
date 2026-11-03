"""Ponte com o motor-lofi (submódulo em external/motor-lofi).

O V5 não copia nem reimplementa o motor: importa os módulos `lofi.*` do submódulo
e chama o renderizador em subprocesso, do mesmo jeito que as skills fazem.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import get_settings


class MotorUnavailable(RuntimeError):
    pass


def motor_dir() -> Path:
    path = get_settings().motor_dir
    if not (path / "lofi" / "__init__.py").is_file():
        raise MotorUnavailable(
            f"motor-lofi não encontrado em {path}. Rode: git submodule update --init --recursive")
    return path


def ensure_importable() -> Path:
    path = motor_dir()
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path


def lofi(module: str):
    """Importa `lofi.<module>` do submódulo. Ex.: lofi('autodirect').compile_story(...)."""
    ensure_importable()
    return importlib.import_module(f"lofi.{module}")


def available_scenes() -> list[str]:
    return sorted(lofi("scenes").REGISTRY)


def list_motor_series() -> list[str]:
    root = motor_dir() / "series"
    return sorted(p.name for p in root.iterdir() if (p / "personagens.json").is_file()) if root.is_dir() else []


def read_motor_series(slug: str) -> dict:
    folder = motor_dir() / "series" / slug
    if not folder.is_dir():
        raise FileNotFoundError(slug)
    def load(name):
        p = folder / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    bible = folder / "biblia.md"
    return {"slug": slug, "dir": str(folder), "characters": load("personagens.json"),
            "world": load("mundo.json"), "bible": bible.read_text(encoding="utf-8") if bible.is_file() else ""}


def ffmpeg_exe() -> str | None:
    """FFmpeg do sistema; sem ele, o binário do pacote imageio-ffmpeg."""
    found = shutil.which(get_settings().ffmpeg)
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def _ffmpeg_bin_dir() -> Path | None:
    """Pasta com um executável chamado `ffmpeg`, para o lofi.render (que chama "ffmpeg" pelo nome)."""
    if shutil.which("ffmpeg"):
        return None
    exe = ffmpeg_exe()
    if not exe:
        return None
    bindir = get_settings().data_dir / "bin"
    target = bindir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not target.exists():
        bindir.mkdir(parents=True, exist_ok=True)
        try:
            os.link(exe, target)          # link: não ocupa espaço
        except OSError:
            shutil.copy2(exe, target)
    return bindir


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(motor_dir()) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    bindir = _ffmpeg_bin_dir()
    if bindir:
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return env


def ffmpeg_available() -> bool:
    return ffmpeg_exe() is not None


def render_frames(spec_path: Path, times: list[float], timeout: int = 300) -> list[Path]:
    """Quadros de prévia (`lofi.render --test`); não exige FFmpeg quando o episódio não tem vozes."""
    args = [sys.executable, "-m", "lofi.render", str(spec_path), "--test", *[f"{t:.3f}" for t in times]]
    res = subprocess.run(args, cwd=motor_dir(), env=_env(), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError("render de prévia falhou: " + res.stderr[-1500:])
    base = spec_path.parent
    return [base / f"_teste_{t:05.1f}.png" for t in times]


def render_video(spec_path: Path, jobs: int = 2, timeout: int = 3 * 3600) -> Path:
    if not ffmpeg_available():
        raise RuntimeError("FFmpeg não encontrado: instale o FFmpeg ou `pip install imageio-ffmpeg`")
    args = [sys.executable, "-m", "lofi.render", str(spec_path)]
    if shutil.which("ffprobe"):
        args += ["--jobs", str(jobs)]     # paralelo com cache confere os blocos com ffprobe
    # sem ffprobe (só o FFmpeg do imageio-ffmpeg): render num processo só, que não precisa dele
    res = subprocess.run(args, cwd=motor_dir(), env=_env(), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError("render falhou: " + res.stderr[-2000:])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out = spec_path.parent / spec.get("out", "episodio.mp4")
    if not out.is_file():
        raise RuntimeError(f"render terminou sem gerar {out}")
    return out
