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

Diretivas de interface (valem para a próxima linha; não viram plano):
  [ALERTA] Permaneça em casa! | Feche portas e janelas.
  [NOTIFICACAO] Família | Pai | Mensagem de voz (0:12)
  [CARTELA] Sábado | 07:00
  [HORA] 07:00
  [STATUS] CEL 58% ; ÁGUA 100% CHEIA
  [MISSAO] Prepare o abrigo | 90:00          ([MISSAO] sozinho esconde a missão)
  [TENSAO]  moldura de retícula     [GLITCH]  falha de imagem no começo do plano
  [SEM INTERFACE]  esconde a interface no próximo plano
Pronomes: "Ele/Ela ..." no começo da narração aponta para o último personagem citado com aquele pronome
(o pronome de cada personagem é cadastrado; nunca é deduzido do nome).
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
    pronoun: str | None = None

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
    overlays: dict = field(default_factory=dict)    # chaves de plano do motor (alerta, cartela, hora, status...)


DIRECTIVE = re.compile(r"^\s*\[\s*([A-Za-zÀ-ÿ ]+?)\s*\]\s*(.*)$")
PRONOUN_START = re.compile(r"^(ele|ela|eles|elas)\b", re.I)


def _status_items(text: str) -> dict:
    """"CEL 58% ; ÁGUA 100% CHEIA" -> {"CEL": [0.58, None], "ÁGUA": [1.0, "CHEIA"]}"""
    out = {}
    for part in re.split(r"[;,]", text):
        m = re.match(r"\s*(.+?)\s+(\d{1,3})\s*%\s*(.*)$", part)
        if m:
            out[m.group(1).strip().upper()] = [int(m.group(2)) / 100, m.group(3).strip().upper() or None]
    return out


def parse_directive(line: str) -> dict | None:
    """Diretiva de interface -> chaves do plano do motor. None se a linha não for diretiva."""
    m = DIRECTIVE.match(line)
    if not m:
        return None
    tag = fold(m.group(1)).replace(" ", "")
    args = [a.strip() for a in m.group(2).split("|")] if m.group(2).strip() else []
    if tag == "alerta":
        return {"alerta": {"titulo": args[0] if args else "ALERTA", "texto": args[1] if len(args) > 1 else ""}}
    if tag in ("notificacao", "mensagem"):
        a = args + ["", "", ""]
        return {"notificacao": {"app": a[0] or "MENSAGEM", "titulo": a[1], "texto": a[2]}}
    if tag == "cartela":
        return {"cartela": {"titulo": args[0] if args else "", **({"sub": args[1]} if len(args) > 1 else {})}}
    if tag == "hora" and args and re.match(r"^\d{1,2}:\d{2}$", args[0]):
        return {"hora": args[0].zfill(5)}
    if tag == "status" and args:
        return {"status": _status_items(" ; ".join(args))}
    if tag == "missao":
        return {"missao": {"titulo": args[0], **({"prazo": args[1]} if len(args) > 1 else {})} if args else None}
    if tag == "tensao":
        return {"reticula": float(args[0]) if args and re.match(r"^[\d.]+$", args[0]) else 0.8}
    if tag == "glitch":
        return {"glitch": True}
    if tag == "seminterface":
        return {"interface": False}
    return None


def build_index(characters: list[dict]) -> list[Who]:
    """characters: [{"slug", "name", "aliases"?}]. Aceita o nome completo, o primeiro nome e o slug."""
    index = []
    for c in characters:
        name = c.get("name") or c["slug"]
        names = [name, c["slug"].replace("_", " "), name.split()[0], *c.get("aliases", [])]
        who = Who(c["slug"], [n for n in names if len(n) >= 2])
        who.pronoun = fold(c.get("pronoun") or "") or None     # "ele"/"ela", só se cadastrado
        index.append(who)
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
    pending: dict = {}
    recent: list[str] = []      # personagens na ordem em que foram citados por último (para pronomes)
    pronoun_of = {w.slug: w.pronoun for w in index}

    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        directive = parse_directive(line)
        if directive is not None:
            for k, v in directive.items():
                if k == "status" and "status" in pending:
                    pending["status"].update(v)
                else:
                    pending[k] = v
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
            pm = PRONOUN_START.match(fold(line))
            if pm and not beat.mentioned:
                pron = pm.group(1).rstrip("s")
                target = next((s for s in reversed(recent) if pronoun_of.get(s) == pron and s in present), None)
                if target:
                    beat.mentioned = [target]
        if pending:
            beat.overlays, pending = pending, {}

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
        for slug in ([speaker] if speaker else []) + beat.mentioned:
            if slug in recent:
                recent.remove(slug)
            recent.append(slug)
        if not speaker and EXIT.search(fold(line)):
            for slug in beat.mentioned:
                if slug in present:
                    present.remove(slug)
    if pending and beats:          # diretivas no fim do roteiro valem para a última linha
        beats[-1].overlays.update(pending)
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
