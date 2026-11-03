"""Workflows ComfyUI no formato API ("Save (API)"), com parâmetros por marcador.

Marcadores aceitos nos valores de entrada:
  "$prompt"            valor inteiro trocado pelo parâmetro (mantém o tipo: int, float, str)
  "texto {{prompt}}"   trecho trocado pelo parâmetro convertido em texto

Metadados opcionais ficam na chave de topo "_lofi" (removida antes do envio):
  {"_lofi": {"kind": "image", "description": "...", "defaults": {"steps": 28}, "required": ["prompt"]}}
"""
import copy
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
WHOLE = re.compile(r"^\$([a-zA-Z_][a-zA-Z0-9_]*)$")
SEED_INPUTS = ("seed", "noise_seed")
MAX_SEED = 2**53 - 1

# Pista de pasta para mensagens de instalação quando /object_info não está disponível.
MODEL_INPUT_FOLDERS = {
    "ckpt_name": "checkpoints", "lora_name": "loras", "vae_name": "vae", "control_net_name": "controlnet",
    "unet_name": "diffusion_models", "clip_name": "text_encoders", "clip_name1": "text_encoders",
    "clip_name2": "text_encoders", "model_name": "upscale_models", "ipadapter_file": "ipadapter",
    "style_model_name": "style_models", "sam_model": "sams", "model_path": "models",
}
# Mesma entrada, pasta diferente conforme o nó.
CLASS_INPUT_FOLDERS = {("CLIPVisionLoader", "clip_name"): "clip_vision"}


class WorkflowError(ValueError):
    pass


@dataclass
class Workflow:
    name: str
    graph: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.meta.get("kind", "image")

    def placeholders(self) -> set[str]:
        found: set[str] = set()
        for _, _, value in _iter_inputs(self.graph):
            if isinstance(value, str):
                m = WHOLE.match(value)
                if m:
                    found.add(m.group(1))
                found.update(PLACEHOLDER.findall(value))
        return found

    def node_classes(self) -> set[str]:
        return {n["class_type"] for n in self.graph.values()}

    def render(self, params: dict[str, Any], *, seed: int | None = None) -> tuple[dict, int]:
        """Devolve (grafo pronto para /prompt, seed usada)."""
        values = {**self.meta.get("defaults", {}), **params}
        if seed is None:
            seed = values.get("seed")
        if seed is None:
            seed = random.randint(0, MAX_SEED)
        seed = int(seed)
        if not 0 <= seed <= MAX_SEED:
            raise WorkflowError(f"seed fora do intervalo 0..{MAX_SEED}")
        values["seed"] = seed

        missing = [p for p in self.meta.get("required", []) if p not in values]
        unresolved = sorted((self.placeholders() - set(values)) | set(missing))
        if unresolved:
            raise WorkflowError("parâmetros ausentes: " + ", ".join(unresolved))

        graph = copy.deepcopy(self.graph)
        seeded = False
        for node_id, name, value in list(_iter_inputs(graph)):
            if not isinstance(value, str):
                continue
            m = WHOLE.match(value)
            if m:
                graph[node_id]["inputs"][name] = values[m.group(1)]
                seeded |= m.group(1) == "seed"
            elif PLACEHOLDER.search(value):
                graph[node_id]["inputs"][name] = PLACEHOLDER.sub(lambda mm: str(values[mm.group(1)]), value)
        if not seeded:
            # Sem marcador $seed: aplica a seed em toda entrada numérica de seed (KSampler etc.).
            for node_id, name, value in _iter_inputs(graph):
                if name in SEED_INPUTS and isinstance(value, int):
                    graph[node_id]["inputs"][name] = seed
        return graph, seed


def _iter_inputs(graph: dict):
    for node_id, node in graph.items():
        for name, value in (node.get("inputs") or {}).items():
            yield node_id, name, value


def parse(data: dict, name: str = "workflow") -> Workflow:
    if not isinstance(data, dict):
        raise WorkflowError("workflow deve ser um objeto JSON")
    if "nodes" in data and "links" in data:
        raise WorkflowError(f"{name}: formato de interface. No ComfyUI use 'Workflow > Export (API)'.")
    meta = data.get("_lofi", {})
    graph = {k: v for k, v in data.items() if k != "_lofi"}
    if not graph:
        raise WorkflowError(f"{name}: workflow vazio")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or "class_type" not in node:
            raise WorkflowError(f"{name}: nó {node_id} sem class_type")
    return Workflow(name, graph, meta)


def load(path: Path) -> Workflow:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WorkflowError(f"{path.name}: JSON inválido ({e})") from e
    return parse(data, path.stem)


def list_workflows(folder: Path) -> list[Workflow]:
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        try:
            out.append(load(p))
        except WorkflowError:
            continue
    return out


def model_references(wf: Workflow, object_info: dict | None = None) -> list[dict]:
    """Entradas que escolhem um arquivo de modelo.

    Com /object_info: toda entrada string cuja definição é uma lista de opções e cujo valor
    tem cara de arquivo. Sem ele: heurística pelos nomes de entrada conhecidos.
    """
    refs = []
    defaults = wf.meta.get("defaults", {})
    for node_id, name, value in _iter_inputs(wf.graph):
        if isinstance(value, str) and (m := WHOLE.match(value)):
            value = defaults.get(m.group(1))   # modelo escolhido por parâmetro: confere o padrão
        if not isinstance(value, str) or WHOLE.match(value) or PLACEHOLDER.search(value):
            continue
        cls = wf.graph[node_id]["class_type"]
        options = None
        if object_info and cls in object_info:
            spec = _input_spec(object_info[cls], name)
            if spec and isinstance(spec[0], list):
                options = spec[0]
        looks_like_file = bool(re.search(r"\.(safetensors|ckpt|pt|pth|bin|gguf|onnx|sft)$", value, re.I))
        if (options is not None and looks_like_file) or (options is None and name in MODEL_INPUT_FOLDERS):
            folder = CLASS_INPUT_FOLDERS.get((cls, name), MODEL_INPUT_FOLDERS.get(name))
            refs.append({"node": node_id, "class_type": cls, "input": name, "value": value,
                         "folder_hint": folder, "options": options})
    return refs


def _input_spec(node_info: dict, name: str):
    inputs = node_info.get("input", {})
    for group in ("required", "optional"):
        if name in inputs.get(group, {}):
            spec = inputs[group][name]
            # formato novo do ComfyUI: ["COMBO", {"options": [...]}]
            if isinstance(spec, list) and spec and spec[0] == "COMBO" and len(spec) > 1:
                return [spec[1].get("options", [])]
            return spec
    return None
