"""Voice Generation Engine: falas com Chatterbox, trilhas por personagem e tempo das cenas pelo áudio.

O Chatterbox roda num Python separado (backend/voice_worker/chatterbox_worker.py). Daqui saem:
  - um WAV por linha, com cache (só refaz o que mudou: texto, amostra de voz ou parâmetros);
  - uma trilha por personagem (falas em sequência), no formato "voices" + "clips" + "subs" do lofi.render;
  - a nova duração de cada cena: a fala inteira cabe no plano, com respiro antes e depois.
"""
import hashlib
import json
import subprocess
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import get_settings

NARRATOR = "narrador"
LEAD_S = 0.15         # respiro antes da fala
TAIL_S = 0.45         # respiro depois da fala
GAP_S = 0.30          # silêncio entre falas na trilha do personagem
SUB_WORDS = 4         # legenda: até 4 palavras por pedaço (regra do validador do motor)

# emoção do plano -> (soma na exaggeration, cfg_weight). Mais exagero pede cfg menor (dica do Chatterbox).
EMOTION_VOICE = {
    "neutral": (0.0, 0.5), "fear": (0.25, 0.35), "angry": (0.35, 0.3), "shock": (0.3, 0.35),
    "sad": (0.05, 0.4), "happy": (0.15, 0.45), "suspicious": (0.05, 0.5), "determined": (0.1, 0.45),
    "crying": (0.2, 0.35), "tired": (-0.1, 0.5),
}


class VoiceError(RuntimeError):
    pass


@dataclass
class Line:
    scene_id: int
    index: int
    voice: str            # slug do personagem (ou "narrador")
    text: str
    emotion: str
    reference: Path | None
    exaggeration: float
    cfg_weight: float
    temperature: float
    seed: int | None
    out: Path

    def cache_key(self) -> str:
        ref = hashlib.sha1(self.reference.read_bytes()).hexdigest() if self.reference else None
        payload = [self.text, self.voice, ref, round(self.exaggeration, 3), round(self.cfg_weight, 3),
                   round(self.temperature, 3), self.seed, get_settings().chatterbox_model,
                   str(get_settings().chatterbox_ckpt_dir or ""), get_settings().voice_language]
        return hashlib.sha1(json.dumps(payload).encode()).hexdigest()


def voice_params(voice: dict, emotion: str) -> tuple[float, float, float]:
    """Parâmetros do personagem ajustados pela emoção do plano: (exaggeration, cfg_weight, temperature)."""
    add, emotion_cfg = EMOTION_VOICE.get(emotion, (0.0, 0.5))
    exaggeration = min(1.2, max(0.25, float(voice.get("exaggeration", 0.5)) + add))
    cfg_weight = min(float(voice.get("cfg_weight", 0.5)), emotion_cfg)
    return round(exaggeration, 3), round(cfg_weight, 3), float(voice.get("temperature", 0.8))


def clean_text(text: str) -> str:
    text = " ".join(text.replace("“", "").replace("”", "").replace('"', "").split())
    if text and text[-1] not in ".!?…":
        text += "."
    return text


# --- execução do worker ---------------------------------------------------------------
def run_chatterbox(lines: list[Line], *, on_event: Callable[[dict], None] | None = None,
                   should_cancel: Callable[[], bool] | None = None, timeout_per_line: float = 180.0) -> dict[int, dict]:
    """Gera os WAVs das linhas. Devolve {posição na lista: evento done/error}."""
    settings = get_settings()
    if not settings.chatterbox_python:
        raise VoiceError("Chatterbox não configurado: defina LOFI_CHATTERBOX_PYTHON (veja docs/VOZES.md)")
    if not Path(settings.chatterbox_worker).is_file():
        raise VoiceError(f"worker não encontrado: {settings.chatterbox_worker}")
    job = {"model": {"t3_model": settings.chatterbox_model, "device": settings.chatterbox_device,
                     "ckpt_dir": str(settings.chatterbox_ckpt_dir) if settings.chatterbox_ckpt_dir else None},
           "items": [{"text": ln.text, "language": settings.voice_language,
                      "reference": str(ln.reference) if ln.reference else None,
                      "exaggeration": ln.exaggeration, "cfg_weight": ln.cfg_weight,
                      "temperature": ln.temperature, "seed": ln.seed, "out": str(ln.out)} for ln in lines]}
    for ln in lines:
        ln.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False)
        job_path = fh.name
    results: dict[int, dict] = {}
    proc = subprocess.Popen([settings.chatterbox_python, str(settings.chatterbox_worker), job_path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    try:
        for raw in proc.stdout:
            if should_cancel and should_cancel():
                proc.kill()
                raise VoiceCancelled()
            try:
                event = json.loads(raw)
            except ValueError:
                continue  # prints de bibliotecas
            if event.get("event") in ("done", "error"):
                results[event["index"]] = event
            if on_event:
                on_event(event)
        proc.wait(timeout=timeout_per_line)
    finally:
        if proc.poll() is None:
            proc.kill()
        Path(job_path).unlink(missing_ok=True)
    if not results:
        err = proc.stderr.read()[-2000:] if proc.stderr else ""
        raise VoiceError(f"worker de voz falhou (código {proc.returncode}): {err}")
    return results


class VoiceCancelled(RuntimeError):
    pass


# --- áudio ------------------------------------------------------------------------------
def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise VoiceError(f"{path.name}: esperado WAV PCM 16 bits")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
        return data, w.getframerate()


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def trim_silence(data: np.ndarray, sr: int, threshold: float = 0.01) -> np.ndarray:
    loud = np.flatnonzero(np.abs(data) > threshold)
    if not len(loud):
        return data
    pad = int(0.04 * sr)
    return data[max(0, loud[0] - pad):min(len(data), loud[-1] + pad)]


def build_tracks(line_files: dict[int, tuple[str, Path]], out_dir: Path) -> tuple[dict[str, Path], dict[int, tuple[float, float]]]:
    """Junta as falas de cada voz num WAV só. Devolve (trilhas, {índice da cena: (início, fim) na trilha})."""
    by_voice: dict[str, list[tuple[int, np.ndarray]]] = {}
    sr_ref = None
    for idx in sorted(line_files):
        voice, path = line_files[idx]
        data, sr = read_wav(path)
        if sr_ref is None:
            sr_ref = sr
        elif sr != sr_ref:
            raise VoiceError("falas com taxas de amostragem diferentes")
        by_voice.setdefault(voice, []).append((idx, trim_silence(data, sr)))
    tracks, spans = {}, {}
    gap = np.zeros(int(GAP_S * (sr_ref or 24000)), dtype=np.float32)
    for voice, items in by_voice.items():
        parts, pos = [], 0
        for idx, data in items:
            spans[idx] = (round(pos / sr_ref, 3), round((pos + len(data)) / sr_ref, 3))
            parts += [data, gap]
            pos += len(data) + len(gap)
        path = out_dir / f"{voice}.wav"
        write_wav(path, np.concatenate(parts), sr_ref)
        tracks[voice] = path
    return tracks, spans


def subtitle_chunks(text: str, words: int = SUB_WORDS) -> list[str]:
    tokens = text.upper().split()
    return [" ".join(tokens[i:i + words]) for i in range(0, len(tokens), words)]


def retime(durations: list[float], speech: dict[int, float]) -> list[tuple[float, float]]:
    """Novos (início, fim) das cenas: cena com fala dura o suficiente para a fala inteira."""
    out, clock = [], 0.0
    for i, dur in enumerate(durations):
        if i in speech:
            dur = max(1.2, LEAD_S + speech[i] + TAIL_S)
        out.append((round(clock, 3), round(clock + dur, 3)))
        clock += dur
    return out
