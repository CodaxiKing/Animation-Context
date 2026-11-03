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
    EngineInfo("story_analysis", "Story Analysis Engine", "ready", "motor-lofi: autodirect + director",
               "Roteiro → planos com duração, enquadramento, emoção e pose."),
    EngineInfo("character_identity", "Character Identity Engine", "ready", "V5: fichas travadas + STYLE_MASTER",
               "Ficha travada por personagem, importada do motor ou cadastrada; prompts sempre com o estilo mestre."),
    EngineInfo("character_assets", "Character Asset Generator", "ready", "ComfyUI: workflow character_sheet",
               "Folha de referência do personagem; requer instância ComfyUI e o workflow instalado."),
    EngineInfo("scene_planning", "Scene Planning Engine", "ready", "V5 + motor-lofi",
               "Planos viram cenas revisáveis; cenário escolhido pelo texto ou pelo cadastro."),
    EngineInfo("environment_generator", "Environment Generator", "ready", "ComfyUI: workflow environment",
               "Arte de fundo do cenário no estilo da série."),
    EngineInfo("image_generation", "Image Generation Engine", "ready", "ComfyUI (provedor plugável)",
               "Qualquer workflow API com marcadores $param / {{param}}."),
    EngineInfo("comfyui_orchestrator", "ComfyUI Orchestrator", "ready", "V5",
               "Várias instâncias, detecção, dependências, progresso, cancelamento, novas tentativas, seeds, VRAM."),
    EngineInfo("image_segmentation", "Image Segmentation Engine", "partial", "motor-lofi: imagens.tirar_fundo",
               "Remove fundo liso; segmentação por IA (SAM/RMBG via ComfyUI) ainda não integrada."),
    EngineInfo("character_motion", "Character Motion Engine", "motor", "motor-lofi: rig + character_motion",
               "Andar, correr, gestos e poses procedurais."),
    EngineInfo("facial_animation", "Facial Animation Engine", "motor", "motor-lofi: rig + facial_timeline",
               "10 emoções, piscar, respiração."),
    EngineInfo("lip_sync", "Lip Sync Engine", "motor", "motor-lofi: rhubarb / visemas",
               "Boca por vogais a partir do áudio."),
    EngineInfo("environment_animation", "Environment Animation Engine", "motor", "motor-lofi: cenario_vivo + fx",
               "Chuva, névoa, paralaxe e detalhes vivos."),
    EngineInfo("cinematic_camera", "Cinematic Camera Engine", "motor", "motor-lofi: render + travelling",
               "Zoom, travelling, câmera na mão, foco seletivo."),
    EngineInfo("video_generation", "Video Generation Engine", "planned", "ComfyUI (workflows de vídeo)",
               "Nenhum workflow de vídeo foi integrado ainda."),
    EngineInfo("voice_generation", "Voice Generation Engine", "ready", "motor-lofi: providers (Piper/ElevenLabs)",
               "Só com provedor escolhido explicitamente; nada pago é chamado por padrão."),
    EngineInfo("sound_design", "Sound Design Engine", "motor", "motor-lofi: audio",
               "Efeitos, ambiência e trilha sintetizados."),
    EngineInfo("timeline_composer", "Timeline Composer", "motor", "motor-lofi: render + audio.mix",
               "Montagem de planos, áudio e legendas."),
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
