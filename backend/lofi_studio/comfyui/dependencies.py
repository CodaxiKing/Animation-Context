"""Gerenciador de dependências: compara o que um workflow exige com o que a instância tem.

Nunca baixa nem instala nada. Só produz um relatório com o que falta e como instalar.
"""
from .workflow import Workflow, model_references

# Prefixos de class_type -> pacote de nós customizados conhecido (orientação de instalação).
KNOWN_NODE_PACKS = [
    (("IPAdapter",), "ComfyUI_IPAdapter_plus", "https://github.com/cubiq/ComfyUI_IPAdapter_plus"),
    (("VHS_",), "ComfyUI-VideoHelperSuite", "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"),
    (("ADE_",), "ComfyUI-AnimateDiff-Evolved", "https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved"),
    (("ControlNetPreprocessor", "OpenposePreprocessor", "DWPreprocessor", "LineArtPreprocessor",
      "CannyEdgePreprocessor", "DepthAnythingPreprocessor"), "comfyui_controlnet_aux",
     "https://github.com/Fannovel16/comfyui_controlnet_aux"),
    (("SAMLoader", "GroundingDino", "SAMModelLoader"), "comfyui_segment_anything",
     "https://github.com/storyicon/comfyui_segment_anything"),
    (("LayeredDiffusion",), "ComfyUI-layerdiffuse", "https://github.com/huchenlei/ComfyUI-layerdiffuse"),
    (("Image Remove Background", "BRIA_RMBG", "RMBG"), "ComfyUI-RMBG", "https://github.com/1038lab/ComfyUI-RMBG"),
    (("InstantID", "ApplyInstantID"), "ComfyUI_InstantID", "https://github.com/cubiq/ComfyUI_InstantID"),
    (("PulidModelLoader", "ApplyPulid"), "ComfyUI_PuLID", "https://github.com/cubiq/PuLID_ComfyUI"),
]


def _pack_for(class_type: str):
    for prefixes, name, url in KNOWN_NODE_PACKS:
        if any(class_type.startswith(p) for p in prefixes):
            return {"pack": name, "url": url}
    return None


def check(wf: Workflow, object_info: dict, installed_models: dict[str, list[str]] | None = None) -> dict:
    """Relatório de compatibilidade de `wf` com uma instância (object_info = GET /object_info)."""
    missing_nodes = []
    for cls in sorted(wf.node_classes()):
        if cls not in object_info:
            missing_nodes.append({"class_type": cls, "install": _pack_for(cls)})

    missing_models = []
    for ref in model_references(wf, object_info):
        options = ref["options"]
        if options is None and installed_models and ref["folder_hint"]:
            options = installed_models.get(ref["folder_hint"])
        if options is not None and ref["value"] not in options:
            folder = ref["folder_hint"] or "models"
            missing_models.append({
                "node": ref["node"], "class_type": ref["class_type"], "input": ref["input"],
                "file": ref["value"], "folder": folder,
                "install": f"Baixe '{ref['value']}' manualmente e coloque em ComfyUI/models/{folder}/ "
                           "(depois clique em Refresh no ComfyUI). Nenhum download é feito automaticamente.",
            })

    hints = []
    if missing_nodes:
        packs = {n["install"]["pack"]: n["install"]["url"] for n in missing_nodes if n["install"]}
        for pack, url in packs.items():
            hints.append(f"Instale o pacote de nós {pack} ({url}) pelo ComfyUI-Manager ou git clone em custom_nodes/.")
        unknown = [n["class_type"] for n in missing_nodes if not n["install"]]
        if unknown:
            hints.append("Nós sem pacote conhecido: " + ", ".join(unknown) +
                         ". Use 'Install Missing Custom Nodes' do ComfyUI-Manager.")
    return {"workflow": wf.name, "compatible": not missing_nodes and not missing_models,
            "missing_nodes": missing_nodes, "missing_models": missing_models, "hints": hints}
