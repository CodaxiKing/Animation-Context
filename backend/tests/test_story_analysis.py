from fastapi.testclient import TestClient

from lofi_studio import keyframes
from lofi_studio.api import app
from lofi_studio.story_analysis import analyze, cast_shot
from test_api import wait_job

CHARS = [{"slug": "joao", "name": "João"}, {"slug": "bia", "name": "Bia"},
         {"slug": "geraldo", "name": "Geraldo Souza"}]
ENVS = [{"slug": "corredor", "name": "corredor"}, {"slug": "mercado", "name": "mercado"}]

SCRIPT = """CENA 1 · CORREDOR
A luz pisca no corredor.
João abre a porta e vê Bia.
BIA (com medo): Fecha isso, agora!
joao: Calma. Fala baixo.
Bia sai correndo.
João fica sozinho, ouvindo.

CENA 2 · MERCADO
Geraldo baixa a porta de aço.
Geraldo: Ninguém entra.
"""


def test_speakers_cast_exits_and_scene_breaks():
    beats = analyze(SCRIPT, CHARS, ENVS)
    assert [b.text for b in beats][:2] == ["A luz pisca no corredor.", "João abre a porta e vê Bia."]
    assert beats[0].cast == [] and beats[0].environment == "corredor"
    assert beats[1].cast == ["joao", "bia"]
    assert (beats[2].speaker, beats[2].note_emotion, beats[2].dialogue) == ("bia", "fear", "Fecha isso, agora!")
    assert beats[2].cast == ["joao", "bia"]                  # ordem estável: João entrou primeiro
    assert beats[3].speaker == "joao"                        # fala em minúsculas, sem acento
    assert beats[4].cast == ["bia"]                          # narração foca em quem a linha cita
    assert beats[5].cast == ["joao"]                         # Bia saiu na linha anterior
    assert beats[6].block == 1 and beats[6].cast == ["geraldo"] and beats[6].environment == "mercado"
    assert beats[7].speaker == "geraldo"                     # primeiro nome de "Geraldo Souza"


def test_dialogue_does_not_change_place():
    envs = ENVS + [{"slug": "radio", "name": "rádio"}]
    beats = analyze("CENA 1 · MERCADO\nGeraldo olha o celular.\nGeraldo: Ninguém responde no rádio.\nBia anda.", CHARS, envs)
    assert [b.environment for b in beats] == ["mercado", "mercado", "mercado"]


def test_narration_with_colon_is_not_dialogue():
    beats = analyze("Ela pega a lanterna: está sem pilha.\nNinguém: nada.", CHARS)
    assert all(b.speaker is None for b in beats)


def test_cast_shot_focuses_speaker_and_keeps_screen_sides():
    beats = analyze(SCRIPT, CHARS, ENVS)
    base = {"actors": [{"char": "joao", "y": 1650, "emotion": "fear", "pose": "idle", "view": "front"}],
            "camera": {"zoom": 1.28}, "director_notes": {"shot": "close"}}
    shot = cast_shot(dict(base, actors=[dict(base["actors"][0])]), beats[2])
    left, right = shot["actors"]
    assert (left["char"], left["view"], left["facing"]) == ("joao", "3q", 1)
    assert (right["char"], right["facing"], right["emotion"]) == ("bia", -1, "fear")
    assert left["emotion"] == "neutral"                      # quem ouve não exagera
    assert shot["camera"] == {"close": 1}
    assert shot["director_notes"]["speaker"] == "bia"

    cfg = keyframes.scene_images(shot, "corredor")
    assert cfg["plano"] == "close" and cfg["alvo"] == 1
    assert cfg["personagens"][1].get("espelhar") and not cfg["personagens"][0].get("espelhar")
    assert cfg["personagens"][0]["img"] == "joao_34_neutro_em_pe"

    empty = cast_shot({"actors": [{"char": "joao"}], "director_notes": {}}, beats[0])
    assert empty["actors"] == []


def test_episode_planning_uses_dialogue_cast():
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "sv", "title": "SV"}).json()["id"]
        for c in CHARS:
            client.post(f"/api/series/{sid}/characters", json={"slug": c["slug"], "name": c["name"]})
        for e in ENVS:
            client.post(f"/api/series/{sid}/environments", json={**e, "motor_scene": e["slug"]})
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()
        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])
        assert job["status"] == "succeeded", job["error"] or job["result"]
        scenes = client.get(f"/api/episodes/{ep['id']}/scenes").json()
        assert len(scenes) == 8
        assert [a["char"] for a in scenes[2]["shot"]["actors"]] == ["joao", "bia"]
        assert scenes[2]["shot"]["director_notes"]["speaker"] == "bia"
        assert scenes[6]["environment"] == "mercado" and scenes[6]["shot"]["scene"] == "mercado"
        assert scenes[0]["shot"]["actors"] == []
        plan = client.get(f"/api/episodes/{ep['id']}/keyframes").json()
        assert {"joao_34_neutro_em_pe", "bia_34_medo_em_pe", "fundo_mercado"} <= set(plan["images"])
