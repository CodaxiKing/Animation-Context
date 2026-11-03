"""Gera workflows/scene_styled_{0,1,2}.json (cena inteira com referência de estilo + 0, 1 ou 2 personagens).

    python tools/gerar_workflows_estilo.py

Uma base só para os três arquivos não divergirem. Estilo: IPAdapter em "style transfer" com a imagem de
referência da série. Personagens: IPAdapter com a âncora de cada um; com dois, cada um limitado à sua metade
da tela por máscara de atenção (attn_mask), na mesma ordem dos lados que o motor usa (esquerda, direita).
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "workflows"
DEFAULTS = {
    "width": 768, "height": 1344, "steps": 30, "cfg": 6.0,
    "ckpt": "sd_xl_base_1.0.safetensors",
    "ipadapter": "ip-adapter-plus_sdxl_vit-h.safetensors",
    "clip_vision": "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
    "style_weight": 0.85, "ip_weight": 0.6, "ip_end": 0.8,
}


def base(n_chars: int) -> dict:
    g = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "$ckpt"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "$prompt", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "$negative", "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": "$width", "height": "$height", "batch_size": 1}},
        "11": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "$ipadapter"}},
        "12": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": "$clip_vision"}},
        "20": {"class_type": "LoadImage", "inputs": {"image": "$style"}},
        "21": {"class_type": "IPAdapterAdvanced", "inputs": {
            "model": ["1", 0], "ipadapter": ["11", 0], "image": ["20", 0], "clip_vision": ["12", 0],
            "weight": "$style_weight", "weight_type": "style transfer", "combine_embeds": "concat",
            "start_at": 0.0, "end_at": 1.0, "embeds_scaling": "V only"}},
    }
    model = ["21", 0]
    refs = ["reference"] if n_chars == 1 else ["reference_a", "reference_b"][:n_chars]
    for i, ref in enumerate(refs):
        load, apply = str(30 + i * 10), str(31 + i * 10)
        g[load] = {"class_type": "LoadImage", "inputs": {"image": f"${ref}"}}
        inputs = {"model": model, "ipadapter": ["11", 0], "image": [load, 0], "clip_vision": ["12", 0],
                  "weight": "$ip_weight", "weight_type": "linear", "combine_embeds": "concat",
                  "start_at": 0.0, "end_at": "$ip_end", "embeds_scaling": "V only"}
        if n_chars == 2:
            mask = str(32 + i * 10)
            side = "mask_a" if i == 0 else "mask_b"
            g[mask] = {"class_type": "LoadImageMask", "inputs": {"image": f"${side}", "channel": "red"}}
            inputs["attn_mask"] = [mask, 0]
        g[apply] = {"class_type": "IPAdapterAdvanced", "inputs": inputs}
        model = [apply, 0]
    g["5"] = {"class_type": "KSampler", "inputs": {
        "model": model, "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        "seed": "$seed", "steps": "$steps", "cfg": "$cfg", "sampler_name": "dpmpp_2m", "scheduler": "karras",
        "denoise": 1.0}}
    g["6"] = {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}}
    g["7"] = {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": f"lofi/scene_styled_{n_chars}"}}
    required = ["prompt", "negative", "style"] + refs + (["mask_a", "mask_b"] if n_chars == 2 else [])
    desc = {0: "Cenário da cena (sem personagens) no estilo da imagem de referência da série.",
            1: "Cena inteira com 1 personagem (identidade pela âncora) no estilo da imagem de referência.",
            2: "Cena inteira com 2 personagens, cada um na sua metade da tela (máscara de atenção), no estilo da "
               "imagem de referência."}[n_chars]
    return {"_lofi": {"kind": "image", "description": desc + " Vertical 9:16 (768x1344), cabe em 8 GB.",
                      "required": required, "defaults": DEFAULTS}, **g}


if __name__ == "__main__":
    for n in (0, 1, 2):
        path = OUT / f"scene_styled_{n}.json"
        path.write_text(json.dumps(base(n), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)
