"""STYLE_MASTER: presets de identidade visual e montagem de prompts.

O preset dark_cinematic_lofi_2d reaproveita o MASTER CHARACTER STYLE e o negativo do
motor-lofi (lofi/imagens.py), que já são usados nas folhas de personagem das séries.
"""
from dataclasses import asdict, dataclass, field

from . import motor

VIEWS = {"front": "front view", "three_quarter": "three-quarter view facing right", "side": "side profile view",
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


COZY_MASTER = """MASTER CHARACTER STYLE — MANDATORY

All characters must follow the exact same visual identity:

ART STYLE:
- Cozy slice-of-life 2D illustration with a subtle survival-drama tension.
- Clean digital line art with soft, even dark-brown outlines.
- Soft cel shading with gentle gradients; warm morning or golden-hour light.
- Detailed, lived-in environments: kitchens, pantries, gated-community streets, suburban houses.
- Pleasant, natural color palette with warm wood, beige, sky blue and soft greens; never neon.

HEAD AND FACE — CRITICAL:
- Perfectly round, smooth cartoon head.
- Soft off-white face with very light warm-gray shading.
- Small black oval eyes, simple expressive black eyebrows, small simple mouth.
- No realistic nose, ears or human facial anatomy.
- Same head shape and face color for every character in the series.

BODY:
- Visible skin uses the same soft off-white tone as the face.
- Natural adult proportions (about 6.5-7 heads tall); children 5-5.5 heads tall. No chibi.
- Realistic everyday clothing with folds and textures.

CHARACTER CONSISTENCY:
- Identical head shape, face color and line style for every character.
- Preserve each character's established hair, clothing and accessories across scenes."""


def _cozy_daylight() -> StylePreset:
    neg = ("realistic skin tones, human facial anatomy, realistic nose, photorealistic, 3D render, anime, chibi, "
           "oversized head, neon colors, harsh contrast, horror gore, dark muddy colors, inconsistent character design")
    return StylePreset(
        id="cozy_daylight_2d",
        name="Cozy Daylight 2D",
        description="Ilustração 2D clara e acolhedora (luz da manhã, cenários detalhados) para sobrevivência com "
                    "tensão no cotidiano.",
        master_prompt=COZY_MASTER,
        short_prompt=("Cozy slice-of-life 2D illustration, clean digital line art with soft dark-brown outlines, soft cel "
                      "shading, warm morning light, detailed lived-in environment, pleasant natural palette. Perfectly "
                      "round off-white cartoon heads, small black oval eyes, simple eyebrows, small mouth, no nose"),
        negative=neg,
        motor_profile="cinematic_cartoon",
        traits=("clean digital line art", "soft cel shading", "warm daylight", "detailed environments",
                "round off-white heads", "minimal black eyes", "natural adult proportions"),
        palette=("#f4efe6", "#d9c7a8", "#a8835a", "#6f8fb5", "#9bb98a", "#3b3a3f", "#c9524a"),
        generation={"width": 832, "height": 1216, "steps": 28, "cfg": 6.0},
    )


GRITTY_MASTER = """MASTER CHARACTER STYLE — MANDATORY

All characters must follow the exact same visual identity:

ART STYLE:
- Gritty hand-drawn 2D comic illustration, post-apocalyptic urban horror.
- Heavy, slightly rough black ink outlines with visible hatching on clothes and shadows.
- Grunge paint texture, scratches and stains on surfaces; detailed decaying urban backgrounds.
- Night and storm moods: heavy rain streaks, wet reflective streets, purple and slate-blue overcast sky.
- Warm practical light accents (flashlights, street lamps, fire) against cold desaturated shadows.
- Cinematic composition with depth: foreground character, mid-ground props, distant city and hills.

HEAD AND FACE — CRITICAL:
- Perfectly round, smooth cartoon head, flat off-white to light-gray face.
- Simple black oval eyes, thick expressive black eyebrows, small simple mouth line.
- No realistic nose, ears or human facial anatomy.
- Hair, stubble and beard drawn with ink strokes and may vary per character.
- Small scratches or dirt on the face are allowed for story moments.

BODY:
- Adult proportions (about 6.5-7 heads tall), worn practical clothing with folds, straps and reflective stripes.
- Visible skin on hands uses a light off-white tone; gloves allowed.

CHARACTER CONSISTENCY:
- Identical head shape, face color and ink style for every character.
- Preserve each character's established hair, clothing and accessories across scenes."""


def _gritty_comic() -> StylePreset:
    neg = ("realistic skin tones, human facial anatomy, realistic nose, photorealistic, 3D render, smooth vector art, "
           "chibi, oversized head, bright cheerful colors, pastel, clean sterile background, text, logo, watermark, "
           "inconsistent character design")
    return StylePreset(
        id="gritty_comic_2d",
        name="Gritty Comic 2D",
        description="Quadrinho 2D sujo e sombrio: traço de tinta grosso, textura grunge, chuva, céu roxo à noite, "
                    "cidade em ruínas e luz quente de lanterna. Vertical para TikTok/Reels/Shorts.",
        master_prompt=GRITTY_MASTER,
        short_prompt=("Gritty hand-drawn 2D comic illustration, heavy rough black ink outlines, hatching, grunge paint "
                      "texture, post-apocalyptic urban horror, heavy rain, wet reflective street, purple and slate-blue "
                      "night sky, warm flashlight glow, cinematic depth. Perfectly round off-white cartoon face, simple "
                      "black oval eyes, thick expressive eyebrows, small mouth, no nose"),
        negative=neg,
        motor_profile="horror_illustrated",
        traits=("heavy ink outlines", "grunge texture", "rainy night", "purple sky", "warm practical lights",
                "round off-white face", "thick eyebrows", "detailed urban decay"),
        palette=("#1c1b24", "#3a3550", "#6b5b8c", "#8a7f99", "#c96b3c", "#e8c27a", "#8f2a2a"),
        generation={"width": 768, "height": 1344, "steps": 30, "cfg": 6.0},
    )


_PRESETS: dict[str, StylePreset] | None = None


def presets() -> dict[str, StylePreset]:
    global _PRESETS
    if _PRESETS is None:
        _PRESETS = {p.id: p for p in (_dark_cinematic(), _cozy_daylight(), _gritty_comic())}
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
