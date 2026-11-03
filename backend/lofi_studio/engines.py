"""Catálogo dos serviços do estúdio e de quem os executa hoje.

status:
  ready    implementado no V5 (ou envolvendo o motor) e coberto por testes
  motor    executado pelo renderizador procedural do motor-lofi durante o render
  partial  existe uma versão básica; a versão generativa ainda não
  planned  ainda não implementado
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EngineInfo:
    id: str
    name: str
    status: str
    provider: str
    notes: str


ENGINES = [
    EngineInfo("story_analysis", "Story Analysis Engine", "ready", "V5: story_analysis + motor-lofi: autodirect",
               "Roteiro → quem fala, quem está em cena, entradas/saídas e cenário; o motor decide duração, "
               "enquadramento, emoção e pose de cada linha."),
    EngineInfo("character_identity", "Character Identity Engine", "ready", "V5: fichas travadas + STYLE_MASTER",
               "Ficha travada por personagem, importada do motor ou cadastrada; prompts sempre com o estilo mestre."),
    EngineInfo("character_assets", "Character Asset Generator", "ready", "ComfyUI: workflow character_sheet",
               "Folha de referência do personagem; requer instância ComfyUI e o workflow instalado."),
    EngineInfo("scene_planning", "Scene Planning Engine", "ready", "V5 + motor-lofi",
               "Planos viram cenas revisáveis com elenco de até 3, lados estáveis na tela, 3/4 frente a frente "
               "e câmera em quem fala (close ou plano médio)."),
    EngineInfo("environment_generator", "Environment Generator", "ready", "ComfyUI: workflow environment",
               "Arte de fundo do cenário no estilo da série."),
    EngineInfo("image_generation", "Image Generation Engine", "ready", "ComfyUI: character_pose (SDXL + IPAdapter)",
               "Imagens-chave: fundo por cenário e pose por personagem com identidade travada pela âncora; "
               "o motor anima a composição (modo imagens)."),
    EngineInfo("comfyui_orchestrator", "ComfyUI Orchestrator", "ready", "V5",
               "Várias instâncias, detecção, dependências, progresso, cancelamento, novas tentativas, seeds, VRAM."),
    EngineInfo("image_segmentation", "Image Segmentation Engine", "partial", "motor-lofi: imagens.importar",
               "Recorta poses geradas sobre fundo cinza liso; segmentação por IA (SAM/RMBG) ainda não integrada."),
    EngineInfo("character_motion", "Character Motion Engine", "motor", "motor-lofi: rig + character_motion",
               "Andar, correr, gestos e poses procedurais."),
    EngineInfo("facial_animation", "Facial Animation Engine", "ready", "motor-lofi (rig) + V5: face + face_inpaint",
               "Rig: 10 emoções, piscar, respiração. Imagens-chave: variantes _piscando e _falando por inpainting "
               "só nos olhos/boca (cabeça achada por círculo), trocadas pelo motor no ritmo da voz."),
    EngineInfo("lip_sync", "Lip Sync Engine", "motor", "motor-lofi: rhubarb / visemas + variante _falando",
               "Rig: boca por vogais. Imagens-chave: boca aberta/fechada pelo volume da voz (sem vogais)."),
    EngineInfo("environment_animation", "Environment Animation Engine", "motor", "motor-lofi: cenario_vivo + fx",
               "Chuva, névoa, paralaxe e detalhes vivos."),
    EngineInfo("cinematic_camera", "Cinematic Camera Engine", "motor", "motor-lofi: render + travelling",
               "Zoom, travelling, câmera na mão, foco seletivo."),
    EngineInfo("video_generation", "Video Generation Engine", "ready", "ComfyUI: Wan 2.2 TI2V 5B (image-to-video)",
               "Anima a prévia das cenas escolhidas (2-5 s, 24 fps, vertical); o clipe entra por cima do render "
               "do motor no export, mantendo o áudio."),
    EngineInfo("voice_generation", "Voice Generation Engine", "ready", "Chatterbox Multilingual (worker local)",
               "Voz clonada por personagem a partir de amostra, emoção do plano na exaggeration, narrador opcional, "
               "cache por fala; cenas reajustadas ao tempo real da fala, com legendas."),
    EngineInfo("sound_design", "Sound Design Engine", "motor", "motor-lofi: audio",
               "Efeitos, ambiência e trilha sintetizados."),
    EngineInfo("timeline_composer", "Timeline Composer", "ready", "V5 (tempo pelas falas) + motor-lofi: audio.mix",
               "Duração das cenas pela fala gerada, clips e legendas no spec; mixagem pelo motor."),
    EngineInfo("quality_control", "Quality Control Engine", "ready", "motor-lofi: studio.quality_report + continuity",
               "Lacunas de tempo, câmera repetitiva, voz/legenda ausentes, continuidade."),
    EngineInfo("rendering", "Rendering Engine", "ready", "motor-lofi: lofi.render + FFmpeg",
               "Prévia de quadro por cena (sem FFmpeg) e vídeo final (com FFmpeg)."),
    EngineInfo("asset_library", "Asset Library", "ready", "V5: tabela asset", "Tudo que é gerado fica registrado."),
    EngineInfo("production_dashboard", "Production Dashboard", "planned", "React + TypeScript",
               "A API já expõe tudo o que o painel precisa."),
]


def catalog() -> list[dict]:
    return [asdict(e) for e in ENGINES]
