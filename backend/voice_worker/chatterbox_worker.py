"""Worker de voz Chatterbox Multilingual (roda no Python que tem torch + chatterbox-tts).

Não importa nada do lofi_studio: a API chama este arquivo em subprocesso, com o Python
configurado em LOFI_CHATTERBOX_PYTHON, e conversa por um JSON de entrada e linhas JSON na saída.

    python chatterbox_worker.py job.json

job.json:
  {"model": {"t3_model": "v3", "ckpt_dir": null, "device": "cuda"},
   "items": [{"text": "...", "language": "pt", "reference": "amostra.wav", "exaggeration": 0.5,
              "cfg_weight": 0.5, "temperature": 0.8, "seed": 7, "out": "linha.wav"}]}

Saída (stdout, uma linha JSON por evento):
  {"event": "loaded", "sr": 24000, "device": "cuda"}
  {"event": "done", "index": 0, "out": "linha.wav", "duration": 2.41}
  {"event": "error", "index": 1, "error": "..."}
"""
import json
import sys
import time


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def main(job_path: str) -> int:
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    import soundfile as sf
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    cfg = job.get("model", {})
    device = cfg.get("device", "cuda")
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    t0 = time.time()
    if cfg.get("ckpt_dir"):
        model = ChatterboxMultilingualTTS.from_local(cfg["ckpt_dir"], device, t3_model=cfg.get("t3_model"))
    else:
        model = ChatterboxMultilingualTTS.from_pretrained(device=device, t3_model=cfg.get("t3_model"))
    emit(event="loaded", sr=model.sr, device=device, seconds=round(time.time() - t0, 1),
         vram_mb=round(torch.cuda.max_memory_allocated() / 2**20) if device == "cuda" else None)

    failures = 0
    for i, item in enumerate(job["items"]):
        try:
            if item.get("seed") is not None:
                torch.manual_seed(int(item["seed"]))
            wav = model.generate(item["text"], language_id=item.get("language", "pt"),
                                 audio_prompt_path=item.get("reference"),
                                 exaggeration=float(item.get("exaggeration", 0.5)),
                                 cfg_weight=float(item.get("cfg_weight", 0.5)),
                                 temperature=float(item.get("temperature", 0.8)))
            audio = wav.squeeze().detach().cpu().numpy()
            sf.write(item["out"], audio, model.sr, subtype="PCM_16")
            emit(event="done", index=i, out=item["out"], duration=round(len(audio) / model.sr, 3))
        except Exception as e:  # uma linha com problema não derruba as outras
            failures += 1
            emit(event="error", index=i, error=f"{type(e).__name__}: {e}")
    return 1 if failures == len(job["items"]) and failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
