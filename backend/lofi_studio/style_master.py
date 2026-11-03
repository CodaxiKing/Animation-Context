"""STYLE_MASTER: presets de identidade visual e montagem de prompts.

O preset dark_cinematic_lofi_2d reaproveita o MASTER CHARACTER STYLE e o negativo do
motor-lofi (lofi/imagens.py), que já são usados nas folhas de personagem das séries.
"""
from dataclasses import asdict, dataclass, field

from . import motor

VIEWS = {"front": "front view", "three_quarter": "three-quarter view", "side": "side profile view",
         "back": "back view"}
EMOTIONS = {"neutral": "neutral expression", "fear": "fearful expression, wide eyes",
            "shock": "shocked expression, mouth open", "angry": "angry expression, furrowed eyebrows",
            "sad": "sad expression", "determined": "determined expression", "happy": "smiling",
            "suspicious": "suspicious look", "crying": "crying", "tired": "tired, out of breath"}


@dataclass(frozen=True)
class StylePreset:
    id: str
    name: str
    description: str
    master_prompt: str               # bloco de estilo obrigatório (personagens)
    short_prompt: str                # versão curta para cenários e quadros de cena
    negative: str
    motor_profile: str               # perfil de pós-processamento do motor (lofi.visual_profiles)
    traits: tuple[str, ...] = ()
    palette: tuple[str, ...] = ()
    generation: dict = field(default_factory=dict)  # padrões de geração (passos, cfg, tamanho)

    def public(self) -> dict:
        return asdict(self)


def _dark_cinematic() -> StylePreset:
    im = motor.lofi("imagens")
    return StylePreset(
        id="dark_cinematic_lofi_2d",
        name="Dark Cinematic Lo-Fi 2D",
        description="Ilustração 2D pintada à mão, cinematográfica e sombria, para suspense, terror e sobrevivência.",
        master_prompt=im.MASTER_STYLE,
        short_prompt=im.ESTILO_PADRAO.split(" Avoid:")[0],
        negative=im.NEGATIVO_MASTER,
        motor_profile="horror_illustrated",
        traits=(
            "hand-painted 2D digital illustration", "dark cinematic aesthetic", "desaturated colors",
            "soft shadows", "dark slightly textured outlines", "proportional adult bodies (6.5-7 heads)",
            "perfectly round heads", "off-white grayish faces and skin", "minimal black eyes",
            "expressive eyebrows", "small simplified mouth", "no realistic nose",
        ),
        palette=("#2b2e3a", "#4a4f63", "#6d6a80", "#8e8a9c", "#b9b4bf", "#d9d5d8", "#7a3b3b"),
        generation={"width": 832, "height": 1216, "steps": 28, "cfg": 6.0},
    )


_PRESETS: dict[str, StylePreset] | None = None


def presets() -> dict[str, StylePreset]:
    global _PRESETS
    if _PRESETS is None:
        p = _dark_cinematic()
        _PRESETS = {p.id: p}
    return _PRESETS


def get(preset_id: str) -> StylePreset:
    try:
        return presets()[preset_id]
    except KeyError:
        raise KeyError(f"preset de estilo desconhecido: {preset_id}. Opções: {', '.join(presets())}") from None


def character_prompt(preset: StylePreset, appearance: str, *, view: str = "front", emotion: str = "neutral",
                     pose: str = "standing, full body", background: str = "plain light-gray background") -> dict:
    if view not in VIEWS:
        raise ValueError(f"vista inválida: {view}")
    if emotion not in EMOTIONS:
        raise ValueError(f"emoção inválida: {emotion}")
    positive = (f"{preset.master_prompt}\n\nCHARACTER:\n{appearance.strip()}\n\n"
                f"SHOT: {VIEWS[view]}, {pose}, {EMOTIONS[emotion]}, {background}.")
    return {"prompt": positive, "negative": preset.negative + ", text, logos, watermark"}


def character_sheet_prompt(preset: StylePreset, appearance: str) -> dict:
    """Folha de referência (frente, 3/4, costas) no modelo já usado pelo motor."""
    im = motor.lofi("imagens")
    return {"prompt": im.MODELO_REFERENCIA.format(personagem=appearance.strip(), roupa="(as described above)",
                                                  negativo=preset.negative),
            "negative": preset.negative + ", text, logos, watermark"}


def environment_prompt(preset: StylePreset, description: str, *, time_of_day: str = "night",
                       weather: str = "", framing: str = "wide establishing shot") -> dict:
    extra = f", {weather}" if weather else ""
    positive = (f"{preset.short_prompt}. Background environment, no characters. {description.strip()}. "
                f"{framing}, {time_of_day}{extra}, cinematic composition, atmospheric depth.")
    return {"prompt": positive, "negative": preset.negative + ", people, characters, text, logos, watermark"}
