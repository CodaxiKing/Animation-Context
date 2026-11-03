"""Checagem de pré-requisitos da máquina de produção.

    python -m lofi_studio.preflight            (na pasta backend)
    GET /api/preflight

Confere tudo o que a produção usa e diz o que falta, sem instalar nem baixar nada.
"""
import json
import shutil
import subprocess
import sys

from . import motor
from .config import get_settings

OK, WARN, FAIL = "ok", "aviso", "falta"
WORKFLOWS = {
    "character_sheet": "folha de referência do personagem",
    "character_pose": "poses com identidade (IPAdapter)",
    "environment": "cenários",
    "face_inpaint": "olhos fechados e boca aberta",
    "harmonize": "uniformizar o quadro inicial (opcional)",
    "scene_full": "cena desenhada inteira para o vídeo (opcional)",
    "wan22_i2v": "vídeo Wan 2.2 (cenas animadas)",
}
MIN_FREE_GB = 25


def _item(name, status, detail, fix=""):
    return {"item": name, "status": status, "detail": detail, "fix": fix}


def run(check_comfy: bool = True, check_voice: bool = True) -> dict:
    s = get_settings()
    items = []
    py = sys.version_info
    items.append(_item("Python", OK if py >= (3, 11) else FAIL, f"{py.major}.{py.minor}.{py.micro}",
                       "use Python 3.11 ou 3.12"))
    try:
        items.append(_item("motor-lofi", OK, f"{len(motor.available_scenes())} cenários em {motor.motor_dir()}"))
    except Exception as e:
        items.append(_item("motor-lofi", FAIL, str(e), "git submodule update --init --recursive"))

    exe = motor.ffmpeg_exe()
    items.append(_item("FFmpeg", OK if exe else FAIL, exe or "não encontrado",
                       "pip install imageio-ffmpeg (ou instale o FFmpeg no PATH)"))
    items.append(_item("ffprobe", OK if shutil.which("ffprobe") else WARN,
                       "render paralelo com cache" if shutil.which("ffprobe") else "render em processo único (mais lento)",
                       "instale o FFmpeg completo (inclui ffprobe) para renderizar mais rápido"))

    s.data_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(s.data_dir).free / 2**30
    items.append(_item("Espaço em disco", OK if free >= MIN_FREE_GB else WARN, f"{free:.1f} GB livres em {s.data_dir}",
                       f"recomendado: {MIN_FREE_GB} GB+ (modelos do ComfyUI ~25 GB, fora os vídeos)"))

    comfy = []
    if check_comfy:
        from .comfyui import ComfyError, get_registry, load
        reg = get_registry()
        reg.discover(s.comfyui_scan_ports)
        instances = reg.refresh_all()
        online = [i for i in instances if i.online]
        if not online:
            items.append(_item("ComfyUI", FAIL, "nenhuma instância online",
                               "abra o ComfyUI (porta 8188) ou defina LOFI_COMFYUI_INSTANCES no .env"))
        for inst in online:
            gpu = (inst.public()["devices"] or [{}])[0]
            items.append(_item(f"ComfyUI {inst.name}", OK,
                               f"{inst.url} · {gpu.get('name', '?')} · {gpu.get('vram_total_mb', '?')} MB VRAM"))
            for name, purpose in WORKFLOWS.items():
                path = s.workflows_dir / f"{name}.json"
                if not path.is_file():
                    continue
                try:
                    rep = reg.compatibility(inst, load(path))
                except ComfyError as e:
                    comfy.append({"workflow": name, "instance": inst.name, "error": str(e)})
                    continue
                rep["purpose"] = purpose
                comfy.append(rep)
                fixes = [f"{m['file']} -> models/{m['folder']}/" for m in rep["missing_models"]] + rep["hints"]
                items.append(_item(f"workflow {name}", OK if rep["compatible"] else FAIL, purpose, " · ".join(fixes)))

    if check_voice:
        if not s.chatterbox_python:
            items.append(_item("Chatterbox (vozes)", WARN, "LOFI_CHATTERBOX_PYTHON não definido",
                               "veja docs/VOZES.md; sem isso o episódio sai sem vozes geradas"))
        else:
            code = ("import json,torch,importlib.util as u;"
                    "print(json.dumps({'cuda':torch.cuda.is_available(),"
                    "'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
                    "'chatterbox':u.find_spec('chatterbox') is not None,'soundfile':u.find_spec('soundfile') is not None}))")
            try:
                res = subprocess.run([s.chatterbox_python, "-c", code], capture_output=True, text=True, timeout=120)
                info = json.loads(res.stdout.strip().splitlines()[-1])
                good = info["chatterbox"] and info["soundfile"]
                items.append(_item("Chatterbox (vozes)", OK if good and info["cuda"] else (WARN if good else FAIL),
                                   f"cuda={info['cuda']} gpu={info['gpu']} chatterbox={info['chatterbox']}",
                                   "" if info["cuda"] else "reinstale o torch com CUDA (docs/VOZES.md)"))
            except Exception as e:
                items.append(_item("Chatterbox (vozes)", FAIL, f"não consegui executar {s.chatterbox_python}: {e}",
                                   "confira o caminho em LOFI_CHATTERBOX_PYTHON"))
    ready = not any(i["status"] == FAIL for i in items)
    return {"ready": ready, "items": items, "workflows": comfy}


def main() -> int:
    report = run()
    marks = {OK: "[ok]   ", WARN: "[aviso]", FAIL: "[FALTA]"}
    for it in report["items"]:
        print(f"{marks[it['status']]} {it['item']}: {it['detail']}")
        if it["fix"] and it["status"] != OK:
            print(f"         -> {it['fix']}")
    print("\nPRONTO PARA PRODUZIR" if report["ready"] else "\nFALTAM ITENS: veja [FALTA] acima")
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
