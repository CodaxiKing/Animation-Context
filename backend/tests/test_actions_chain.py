import io
import json
import sys

import pytest
from conftest import FakeComfy, figure_png
from fastapi.testclient import TestClient
from PIL import Image

from lofi_studio import compositor, config, motor, pipeline, preflight
from lofi_studio.actions import is_action_scene, steps_for
from lofi_studio.api import app
from lofi_studio.comfyui import registry
from lofi_studio.models import Scene
from lofi_studio.produzir import Produtor
from test_video import _make
from test_voices import FAKE_WORKER, _wav_bytes


@pytest.mark.parametrize("text,expected", [
    ("Ela toma um gole de café e anda até a mesa.", [("beber", "takes a sip"), ("andar", "walks to the table")]),
    ("João corre pela rua, depois se esconde atrás do carro.",
     [("correr", "along the street"), ("esconder", "behind the car")]),
    ("Bia abre a geladeira e pega o leite.", [("abrir", "opens the fridge"), ("pegar", "picks up the leite")]),
    ("Ele pedala em direção ao portão.", [("pedalar", "to the gate")]),
    ("Ela olha o celular.", [("celular", "phone screen")]),
    ("Ele liga para o pai.", [("ligar", "phone to the ear")]),
    ("Marcos senta à mesa e come.", [("sentar", "at the table"), ("comer", "eats")]),
])
def test_action_steps(text, expected):
    steps = steps_for(text)
    assert [(s.key, frag in s.motion) for s, (_, frag) in zip(steps, expected)] == [(k, True) for k, _ in expected]


def test_no_action_in_plain_lines():
    for text in ("Vai ficar tudo bem.", "O corredor está escuro.", "A energia caiu às sete."):
        assert not is_action_scene(steps_for(text)), text
    assert steps_for("Ela toma café.")[0].start_pose == "segurando_caneca"


def test_clip_plan_splits_long_steps_and_keeps_order():
    sc = Scene(episode_id=1, index=0, start=0, end=12, text="x", shot={"director_notes": {"actions": [
        {"motion": "lifts the mug", "seconds": 3.0}, {"motion": "walks to the table", "seconds": 3.0}]}})
    plan = pipeline.clip_plan(sc)
    assert [p["motion"] for p in plan] == ["lifts the mug", "continues: lifts the mug",
                                           "walks to the table", "continues: walks to the table"]
    assert all(p["seconds"] <= pipeline.MAX_CLIP_SECONDS for p in plan)
    talk = Scene(episode_id=1, index=1, start=0, end=2, text="y", shot={"director_notes": {"speaker": "bia"}})
    assert "speaks" in pipeline.clip_plan(talk)[0]["motion"]


def test_concat_and_last_frame(tmp_path):
    a = _make(tmp_path / "a.mp4", "red", 1, "160x284", 24, False)
    b = _make(tmp_path / "b.mp4", "blue", 1, "160x284", 24, False)
    out = compositor.concat([a, b], tmp_path / "ab.mp4")
    assert abs(compositor.probe(out).duration - (2 - 1 / 24)) < 0.1      # 1º quadro de b sai (repetia a)
    last = compositor.last_frame(b, tmp_path / "last.png")
    assert Image.open(last).convert("RGB").getpixel((80, 140))[2] > 150


def test_preflight_basics():
    rep = preflight.run(check_comfy=False, check_voice=False)
    status = {i["item"]: i["status"] for i in rep["items"]}
    assert status["motor-lofi"] == "ok" and status["FFmpeg"] == "ok" and status["Python"] == "ok"


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), (180, 180, 185)).save(buf, "PNG")
    return buf.getvalue()


def test_producao_ponta_a_ponta(fake_registry, monkeypatch, tmp_path):
    """Arquivo de produção -> episódio exportado, com clipes encadeados na cena de ação."""
    monkeypatch.setenv("LOFI_COMFYUI_WEBSOCKET", "false")
    monkeypatch.setenv("LOFI_CHATTERBOX_PYTHON", sys.executable)
    monkeypatch.setenv("LOFI_CHATTERBOX_WORKER", str(FAKE_WORKER))
    config.get_settings.cache_clear()
    clip = _make(tmp_path / "wan.mp4", "blue", 2, "480x832", 24, False)
    fake = fake_registry.add_fake("gpu", FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"],
                                                   image_bytes=figure_png(), video_bytes=clip.read_bytes()))
    monkeypatch.setattr(registry, "_registry", fake_registry)

    prod = tmp_path / "producao"
    (prod / "refs").mkdir(parents=True)
    (prod / "refs" / "bia.png").write_bytes(_png_bytes())
    (prod / "bia.wav").write_bytes(_wav_bytes())
    (prod / "ep01.txt").write_text("CENA 1 · COZINHA\nBia toma um gole de café e anda até a mesa.\n"
                                   "BIA (com medo): Ninguém sai daqui.\n", encoding="utf-8")
    cfg = {"series": {"slug": "cafe", "title": "CAFÉ"},
           "characters": [{"slug": "bia", "name": "Bia", "description": "young woman, ponytail",
                           "reference": "refs/bia.png", "voice_sample": "bia.wav"}],
           "environments": [{"slug": "cozinha", "name": "cozinha", "description": "cozy kitchen at sunrise",
                             "motor_scene": "apartamento"}],
           "episode": {"number": 1, "title": "Sábado", "script_file": "ep01.txt"},
           "steps": {"clips": "action", "harmonize": True}}
    log = []
    with TestClient(app) as client:
        report = Produtor(client, prod, log=log.append).run(cfg)
        # a cena de ação virou 2 clipes encadeados (beber -> andar), a fala ficou com imagem + voz
        assert list(report["clips"]["clips"]) == ["0"] and report["clips"]["segments"] == 2
        wan = [g for g in fake.prompts.values()
               if g.get("11", {}).get("class_type") == "Wan22ImageToVideoLatent"]
        assert "takes a sip" in wan[0]["5"]["inputs"]["text"] and "walks to the table" in wan[1]["5"]["inputs"]["text"]
        assert wan[1]["10"]["inputs"]["image"] != wan[0]["10"]["inputs"]["image"]    # parte 2 parte do último quadro
        assert any("harmonized" in g.get("7", {}).get("inputs", {}).get("filename_prefix", "")
                   for g in fake.prompts.values())
        assert report["voices"]["generated"] == 1
        assert report["export"]["video_clips"] == 1
        scenes = client.get("/api/episodes/1/scenes").json()
        assert scenes[0]["shot"]["actors"][0]["action_pose"] == "segurando_caneca"
        assert scenes[0]["shot"]["video_parts"] == 2
        assert compositor.probe(__import__("pathlib").Path(report["export"]["video"])).has_audio
        # rodar de novo reaproveita tudo (não recria série, personagem, referência ou falas)
        again = Produtor(client, prod, log=log.append).run({**cfg, "steps": {"clips": False, "export": False}})
        assert again["voices"]["cached"] == 1 and again["keyframes"]["generated"] == []
