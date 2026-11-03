"""Substituto do chatterbox_worker.py nos testes: mesmo protocolo, sem torch.

Gera um tom com 0,25 s por palavra, com silêncio antes e depois (que o V5 deve aparar).
Texto com "FALHA" devolve erro, para testar falhas por linha.
"""
import json
import math
import struct
import sys
import wave

SR = 24000


def main(job_path):
    job = json.load(open(job_path, encoding="utf-8"))
    print("biblioteca qualquer imprimindo texto solto", flush=True)
    print(json.dumps({"event": "loaded", "sr": SR, "device": "cpu"}), flush=True)
    for i, item in enumerate(job["items"]):
        if "FALHA" in item["text"]:
            print(json.dumps({"event": "error", "index": i, "error": "RuntimeError: falha simulada"}), flush=True)
            continue
        speech = 0.25 * len(item["text"].split())
        n_sil, n_sp = int(0.3 * SR), int(speech * SR)
        frames = [0] * n_sil + [int(9000 * math.sin(2 * math.pi * 220 * k / SR)) for k in range(n_sp)] + [0] * n_sil
        with wave.open(item["out"], "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(struct.pack(f"<{len(frames)}h", *frames))
        print(json.dumps({"event": "done", "index": i, "out": item["out"], "duration": speech + 0.6}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
