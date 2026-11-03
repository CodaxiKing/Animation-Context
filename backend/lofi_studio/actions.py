"""Ações em sequência: uma linha do roteiro vira os movimentos que o vídeo (Wan 2.2) deve mostrar.

    "Ela toma um gole de café e anda até a mesa."
      -> [beber: "takes a sip of coffee from the mug", andar: "walks to the table"]

Cada passo vira um clipe; clipes da mesma cena são encadeados (o último quadro de um é o
primeiro do próximo). O primeiro passo também define a pose do quadro inicial da cena
(ex.: beber -> segurando a caneca), gerada com a identidade travada do personagem.

Sem LLM: tabela de verbos (presente, passado, gerúndio, plural) e de lugares/objetos comuns.
O que não está na tabela segue em português no prompt (o codificador de texto do Wan é multilíngue).
"""
import re
from dataclasses import asdict, dataclass

from .story_analysis import fold

# chave -> (padrão do verbo, movimento em inglês, pose inicial, segundos sugeridos, é ação "de movimento")
# {X} no movimento recebe o complemento traduzido ("até a mesa" -> "to the table").
VEHICLES = {"carro": "car", "moto": "motorcycle", "onibus": "bus", "caminhao": "truck", "viatura": "police car",
            "ambulancia": "ambulance"}
VEHICLE_ACTION = re.compile(r"\b(" + "|".join(VEHICLES) + r")\b.*?\b(?:pass\w*|corr\w*|acelera\w*|vem|vindo|chega\w*"
                            r"|sai\w*|freia\w*|derrap\w*)")
VERBS: list[tuple[str, str, str, str, float, bool]] = [
    ("beber", r"(?:tom|beb)\w*(?: (?:um|uma) (?:gole|xicara|caneca))?(?: de)? cafe|beb(?:e|em|eu|endo)|gole",
     "lifts the coffee mug and takes a sip", "segurando_caneca", 3.0, False),
    ("comer", r"\bcom(?:e|em|eu|endo)\b|\bmastig\w*", "eats, bringing food to the mouth", "sentado", 3.0, False),
    ("correr", r"\bcorr(?:e|em|eu|eram|endo)\b|\bfog(?:e|em)\b|\bfug(?:iu|indo)\b|\bdispar(?:a|am|ou|ando)\b",
     "runs fast{X}, arms pumping", "correndo", 3.0, True),
    ("pedalar", r"\bpedal\w*|\bbicicleta|\bbike\b", "rides the bicycle forward{X}, pedaling hard", "pedalando", 3.5, True),
    ("dirigir", r"\bdirig\w*|\bacelera\w*", "drives the car{X}, hands on the steering wheel", "dirigindo", 3.5, True),
    ("andar", r"\b(?:and|caminh)(?:a|am|ou|aram|ando)\b|\b(?:vai|vao|foi|foram) (?:ate|para|pela|pelo)\b"
              r"|\bse aproxim\w*|\bvolt(?:a|am|ou) (?:para|ate)\b", "walks{X}", "andando", 3.0, True),
    ("sentar", r"\bsent(?:a|am|ou|ando)\b|\bse senta\b", "sits down{X}", "em_pe", 2.5, False),
    ("levantar", r"\blevant(?:a|am|ou|ando)\b|\bse levanta\b", "stands up{X}", "sentado", 2.5, False),
    ("abrir", r"\babr(?:e|em|iu|indo)\b", "opens the {O}", "em_pe", 2.5, False),
    ("fechar", r"\bfech(?:a|am|ou|ando)\b", "closes the {O}", "em_pe", 2.5, False),
    ("pegar", r"\b(?:peg|agarr)(?:a|am|ou|ando)\b|\btir(?:a|am|ou) (?:o|a|os|as) \w+ d", "picks up the {O}", "em_pe", 2.5, False),
    ("ligar", r"\blig(?:a|am|ou|ando) para\b|\btelefon\w*", "holds the phone to the ear and talks",
     "celular", 3.0, False),
    ("celular", r"\bcelular\b|\bmensage\w*", "looks down at the phone screen, scrolling with the thumb",
     "celular", 2.5, False),
    ("olhar", r"\b(?:olh|observ|espi)(?:a|am|ou|ando)\b", "looks around carefully{X}", "em_pe", 2.5, False),
    ("esconder", r"\bescond(?:e|em|eu|endo)\b|\b(?:agach|abaix)(?:a|am|ou|ando)\b", "crouches down and hides{X}",
     "agachado", 2.5, True),
    ("pular", r"\bpul(?:a|am|ou|ando)\b|\bsalt(?:a|am|ou|ando)\b", "jumps{X}", "correndo", 2.0, True),
    # só queda de gente ("a energia caiu" não é ação de personagem)
    ("cair", r"\bca(?:i|em|iu|indo) (?:no chao|de costas|de joelhos|para tras|sentad[ao])\b|\btrope\w*|\bdesab\w*",
     "falls to the ground", "em_pe", 2.0, True),
    ("chorar", r"\bchor\w*", "cries, shoulders shaking", "em_pe", 3.0, False),
    ("gritar", r"\bgrit\w*", "shouts with the mouth wide open", "em_pe", 2.0, False),
    ("abracar", r"\babra(?:c|ç)\w*", "hugs the other person", "em_pe", 3.0, False),
]

PLACES = {
    "mesa": "the table", "porta": "the door", "janela": "the window", "geladeira": "the fridge",
    "carro": "the car", "rua": "the street", "cozinha": "the kitchen", "sofa": "the couch", "cama": "the bed",
    "portao": "the gate", "escada": "the stairs", "corredor": "the hallway", "sala": "the living room",
    "quarto": "the bedroom", "mercado": "the market", "caixa": "the box", "mochila": "the backpack",
    "lanterna": "the flashlight", "radio": "the radio", "celular": "the phone", "chave": "the keys",
    "caneca": "the mug", "garagem": "the garage", "banheiro": "the bathroom", "telhado": "the roof",
    "camera": "the camera", "estrada": "the road", "fogao": "the stove", "pia": "the sink",
}
START_POSES = {
    "segurando_caneca": "standing, holding a coffee mug at chest height with both hands, full body",
    "pedalando": "riding a bicycle toward the camera, full body",
    "dirigindo": "sitting in the driver's seat, hands on the steering wheel",
}
SPLIT = re.compile(r"\s*(?:,\s*)?\b(?:e depois|depois|e entao|entao|em seguida|e)\b\s*|\s*[;,]\s*", re.I)


@dataclass
class Step:
    key: str
    motion: str          # prompt de movimento em inglês
    start_pose: str      # pose do quadro inicial (nome do lofi/imagens.py)
    seconds: float
    moving: bool         # corre, anda, pedala... (cena "de ação")

    def public(self) -> dict:
        return asdict(self)


def _complement(clause: str) -> tuple[str, str]:
    """(" to the table", "table") a partir de "até a mesa", "para a porta", "a geladeira"."""
    low = fold(clause)
    for word, en in PLACES.items():
        m = re.search(rf"\b(ate|para|pela|pelo|na|no|atras d[oa]|em direcao a|de volta (?:a|para))?\s*"
                      rf"(?:a|o|as|os|ao)?\s*{word}\b", low)
        if m:
            prep, obj = m.group(1) or "", en.replace("the ", "")
            if prep in ("pela", "pelo"):
                return f" along {en}", obj
            if prep in ("na", "no"):
                return f" in {en}", obj
            if prep.startswith("atras"):
                return f" behind {en}", obj
            return (f" to {en}" if prep else f" at {en}"), obj
    # objeto fora da tabela: segue em português ("pega o leite" -> "picks up the leite")
    m = re.search(r"\b(?:o|a|os|as|um|uma)\s+([a-z]{3,})\b", low)
    return "", (m.group(1) if m else "object")


def steps_for(text: str, max_steps: int = 3) -> list[Step]:
    """Passos de movimento de uma linha, na ordem em que aparecem."""
    clauses = [c for c in SPLIT.split(fold(text)) if c and c.strip()]
    steps: list[Step] = []
    for clause in clauses:
        veh = VEHICLE_ACTION.search(clause)
        if veh:   # o veículo é quem age ("um carro passa correndo pela rua"): não é pose de personagem
            comp, _ = _complement(clause.replace(veh.group(1), " "))
            steps.append(Step("veiculo", f"the {VEHICLES[veh.group(1)]} speeds by{comp}, headlights on", "", 3.0, True))
            continue
        for key, pattern, motion, pose, seconds, moving in VERBS:
            if re.search(pattern, clause):
                comp, obj = _complement(clause)
                motion_text = motion.replace("{X}", comp).replace("{O}", obj or "door")
                if not steps or steps[-1].key != key:
                    steps.append(Step(key, motion_text, pose, seconds, moving))
                break
        if len(steps) >= max_steps:
            break
    return steps


def is_action_scene(steps: list[Step]) -> bool:
    return any(s.moving for s in steps)
