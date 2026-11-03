"""Story Analysis Engine: quem fala e quem está em cena em cada linha do roteiro.

O compilador do motor (lofi.autodirect) decide enquadramento, duração, emoção e pose de cada
linha, mas põe um único ator em todos os planos. Este módulo lê o roteiro antes e depois
completa os planos com o elenco certo:

  CENA 2 · CORREDOR                    quebra de cena (zera quem está presente)
  João abre a porta e vê Bia.          narração: João e Bia entram em cena
  BIA (com medo): Fecha isso!          fala da Bia, com emoção
  João: Calma.                         fala do João
  Bia sai correndo.                    Bia deixa a cena depois desta linha

Formatos de fala aceitos: "NOME: fala", "Nome (emoção): fala", "Nome — fala".
Cabeçalhos de cena: "CENA ...", "## ...", "INT."/"EXT.".
"""
import re
import unicodedata
from dataclasses import dataclass, field

MAX_CAST = 3
SCENE_HEADING = re.compile(r"^\s*(?:#{1,3}\s+|cena\b|int\.|ext\.|int/ext\.)", re.I)
DIALOGUE = re.compile(r"^\s*(?P<name>[^\W\d_][\w .'-]{0,40}?)\s*(?:\((?P<note>[^)]{1,60})\))?\s*(?::|—|–|-{2})\s*(?P<line>\S.*)$")
EXIT = re.compile(r"\b(sai|saiu|saem|sa[ií]ram|vai embora|foi embora|desaparece|desapareceu|some|sumiu|"
                  r"deixa a sala|deixa o quarto|fica para tr[aá]s)\b", re.I)
EMOTION_NOTES = (
    (r"medo|assust|apavor|p[aâ]nico|tremend|sussurr", "fear"),
    (r"chor|trist|luto", "sad"),
    (r"raiva|grit|furios|irrit", "angry"),
    (r"sorr|feliz|alegr|ri\b|rindo", "happy"),
    (r"choc|surpres|espant", "shock"),
    (r"desconfi|suspeit", "suspicious"),
    (r"determin|firme|decid", "determined"),
    (r"cansad|ofegant", "tired"),
)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()


def emotion_from_note(note: str | None) -> str | None:
    if not note:
        return None
    low = fold(note)
    return next((emo for pat, emo in EMOTION_NOTES if re.search(fold(pat), low)), None)


@dataclass
class Who:
    slug: str
    names: list[str]

    def pattern(self) -> re.Pattern:
        alts = sorted({fold(n) for n in self.names if n}, key=len, reverse=True)
        return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)")


@dataclass
class Beat:
    text: str                       # linha passada ao compilador do motor (uma linha = um plano)
    block: int                      # índice da cena (bloco entre cabeçalhos)
    speaker: str | None = None
    dialogue: str | None = None
    note_emotion: str | None = None
    mentioned: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)   # quem aparece, em ordem estável de entrada na cena
    environment: str | None = None
    heading: str | None = None


def build_index(characters: list[dict]) -> list[Who]:
    """characters: [{"slug", "name", "aliases"?}]. Aceita o nome completo, o primeiro nome e o slug."""
    index = []
    for c in characters:
        name = c.get("name") or c["slug"]
        names = [name, c["slug"].replace("_", " "), name.split()[0], *c.get("aliases", [])]
        index.append(Who(c["slug"], [n for n in names if len(n) >= 2]))
    return index


def _mentions(text: str, index: list[Who]) -> list[str]:
    low = fold(text)
    hits = []
    for who in index:
        m = who.pattern().search(low)
        if m:
            hits.append((m.start(), who.slug))
    return [slug for _, slug in sorted(hits)]


def _speaker(name: str, index: list[Who]) -> str | None:
    low = fold(name.strip())
    for who in index:
        if who.pattern().fullmatch(low):
            return who.slug
    return None


def _environment(text: str, environments: list[dict]) -> str | None:
    low = fold(text)
    for env in environments:
        for term in {env["slug"].replace("_", " "), env.get("name", "")}:
            if term and re.search(r"(?<!\w)" + re.escape(fold(term)) + r"(?!\w)", low):
                return env["slug"]
    return None


def analyze(script: str, characters: list[dict], environments: list[dict] | None = None) -> list[Beat]:
    index = build_index(characters)
    environments = environments or []
    beats: list[Beat] = []
    block, present, environment, heading = 0, [], None, None

    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if SCENE_HEADING.match(line):
            if beats:
                block += 1
            present, heading = [], line
            environment = _environment(line, environments) or environment
            continue

        beat = Beat(text=line, block=block, heading=heading)
        m = DIALOGUE.match(line)
        speaker = _speaker(m.group("name"), index) if m else None
        if speaker:
            beat.speaker, beat.dialogue = speaker, m.group("line").strip()
            beat.note_emotion = emotion_from_note(m.group("note"))
            # o compilador do motor lê a emoção pelo texto: a rubrica entra na linha
            beat.text = f"{m.group('name').strip()}{' (' + m.group('note') + ')' if m.group('note') else ''}: {beat.dialogue}"
            beat.mentioned = [s for s in _mentions(beat.dialogue, index) if s != speaker]
        else:
            beat.mentioned = _mentions(line, index)

        # o lugar vem do cabeçalho e da narração; fala cita lugares sem mudar de cena ("ninguém responde no rádio")
        beat.environment = (None if speaker else _environment(line, environments)) or environment
        environment = beat.environment

        for slug in ([speaker] if speaker else []) + beat.mentioned:
            if slug not in present:
                present.append(slug)

        if speaker:
            cast = list(present)      # diálogo: todo mundo que está na cena
        elif beat.mentioned:
            cast = [s for s in present if s in beat.mentioned]   # narração: quem a linha cita
        else:
            cast = list(present)
        if speaker and speaker not in cast[:MAX_CAST]:
            cast = [speaker] + [s for s in cast if s != speaker]
        beat.cast = cast[:MAX_CAST]

        beats.append(beat)
        if not speaker and EXIT.search(fold(line)):
            for slug in beat.mentioned:
                if slug in present:
                    present.remove(slug)
    return beats


def compiler_text(beats: list[Beat]) -> str:
    """Texto para lofi.autodirect.compile_story: uma linha por beat, sem cabeçalhos."""
    return "\n".join(b.text for b in beats)


# --- elenco no plano do motor --------------------------------------------------------
X_POSITIONS = {1: [540], 2: [360, 720], 3: [250, 540, 830]}


def cast_shot(shot: dict, beat: Beat) -> dict:
    """Troca o ator único do compilador pelo elenco do beat, com quem fala em foco.

    Ordem e lado na tela ficam estáveis dentro da cena (regra dos 180°): quem entrou primeiro
    fica à esquerda. Em dupla ou trio, os das pontas ficam em 3/4 virados para o centro.
    """
    if not beat.cast:
        shot["actors"] = []          # plano de ambientação: ninguém em cena ainda
        _note(shot, beat, None)
        return shot
    base = (shot.get("actors") or [{}])[0]
    n = len(beat.cast)
    acting = {beat.speaker} | set(beat.mentioned) if (beat.speaker or beat.mentioned) else set(beat.cast)
    actors = []
    for i, slug in enumerate(beat.cast):
        lead = slug in acting
        actor = {"char": slug, "x": X_POSITIONS[n][i], "y": base.get("y", 1650),
                 "emotion": base.get("emotion", "neutral") if lead else "neutral",
                 "pose": base.get("pose", "idle") if lead else "idle",
                 "walk": bool(base.get("walk")) and lead, "run": bool(base.get("run")) and lead,
                 "look": base.get("look", 0) if lead else 0}
        if slug == beat.speaker and beat.note_emotion:
            actor["emotion"] = beat.note_emotion
        if n == 1 or (n == 3 and i == 1):
            actor["view"] = base.get("view", "front")
        else:
            actor["view"], actor["facing"] = "3q", 1 if i == 0 else -1
        actors.append(actor)
    shot["actors"] = actors

    focus = beat.cast.index(beat.speaker) if beat.speaker in beat.cast else None
    if n > 1 and focus is not None:
        kind = shot.get("director_notes", {}).get("shot")
        if kind in ("close", "detail"):
            shot["camera"] = {"close": focus}
        elif kind == "wide":
            shot["camera"] = {"plano": "conjunto"}
        else:
            shot["camera"] = {"plano": "medio", "alvo": focus}
    _note(shot, beat, focus)
    return shot


def _note(shot: dict, beat: Beat, focus: int | None) -> None:
    notes = shot.setdefault("director_notes", {})
    notes.update({"speaker": beat.speaker, "dialogue": beat.dialogue, "cast": beat.cast, "focus": focus,
                  "scene_block": beat.block})
