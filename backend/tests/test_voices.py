import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from lofi_studio import config, db, voices
from lofi_studio.api import app
from test_api import wait_job

FAKE_WORKER = Path(__file__).with_name("fake_voice_worker.py")

SCRIPT = """CENA 1 · CORREDOR
A luz pisca no corredor.
João abre a porta e vê Bia.
BIA (com medo): Fecha isso, agora!
João: Calma. Fala baixo, por favor.
"""


def _wav_bytes(seconds=1.0, sr=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.sin(np.arange(int(seconds * sr)) / 8) * 8000).astype(np.int16).tobytes())
    return buf.getvalue()


@pytest.fixture
def voice_env(monkeypatch):
    monkeypatch.setenv("LOFI_CHATTERBOX_PYTHON", sys.executable)
    monkeypatch.setenv("LOFI_CHATTERBOX_WORKER", str(FAKE_WORKER))
    config.get_settings.cache_clear()


def test_emotion_raises_exaggeration_and_lowers_cfg():
    assert voices.voice_params({}, "neutral") == (0.5, 0.5, 0.8)
    exag, cfg, _ = voices.voice_params({"exaggeration": 0.6}, "angry")
    assert exag == 0.95 and cfg == 0.3
    assert voices.voice_params({"exaggeration": 1.1}, "angry")[0] == 1.2   # limite


def test_retime_fits_speech_and_keeps_order():
    times = voices.retime([2.0, 3.0, 2.5], {1: 4.0})
    assert times == [(0.0, 2.0), (2.0, 6.6), (6.6, 9.1)]
    assert voices.subtitle_chunks("Fecha isso, agora mesmo, por favor!") == ["FECHA ISSO, AGORA MESMO,", "POR FAVOR!"]


def test_tracks_trim_silence_and_report_spans(tmp_path):
    sr = 24000
    a = np.concatenate([np.zeros(sr // 2), np.full(sr, 0.3), np.zeros(sr // 2)]).astype(np.float32)
    voices.write_wav(tmp_path / "a.wav", a, sr)
    voices.write_wav(tmp_path / "b.wav", a, sr)
    tracks, spans = voices.build_tracks({0: ("joao", tmp_path / "a.wav"), 3: ("joao", tmp_path / "b.wav")}, tmp_path / "out")
    assert set(tracks) == {"joao"}
    (s0, e0), (s1, e1) = spans[0], spans[3]
    assert 1.0 <= e0 - s0 <= 1.1                       # silêncio aparado (+ 40 ms de margem)
    assert s1 == pytest.approx(e0 + voices.GAP_S, abs=0.01)


def test_missing_column_is_added_to_existing_table(tmp_path):
    with db.get_engine().begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS legacy_probe (id INTEGER PRIMARY KEY)"))
        conn.execute(text('ALTER TABLE "character" RENAME TO character_old'))
        conn.execute(text('CREATE TABLE "character" (id INTEGER PRIMARY KEY, series_id INTEGER, slug VARCHAR, '
                          'name VARCHAR, description VARCHAR, rig JSON NOT NULL, locked BOOLEAN, '
                          'reference_assets JSON NOT NULL)'))
    db.init_db()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("character")}
    assert "voice" in cols


def test_voices_generated_cached_and_scenes_retimed(voice_env):
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "vz", "title": "VZ"}).json()["id"]
        ids = {}
        for slug, name in (("joao", "João"), ("bia", "Bia"), ("narrador", "Narrador")):
            ids[slug] = client.post(f"/api/series/{sid}/characters", json={"slug": slug, "name": name}).json()["id"]
        client.post(f"/api/series/{sid}/environments", json={"slug": "corredor", "name": "corredor",
                                                              "motor_scene": "corredor"})
        for slug in ("joao", "narrador"):
            r = client.post(f"/api/characters/{ids[slug]}/voice-sample",
                            files={"file": (f"{slug}.wav", _wav_bytes(), "audio/wav")})
            assert r.status_code == 201 and r.json()["voice"]["provider"] == "chatterbox"
        assert client.post(f"/api/characters/{ids['bia']}/voice-sample",
                           files={"file": ("x.txt", b"oi", "text/plain")}).status_code == 422
        assert client.put(f"/api/characters/{ids['joao']}/voice", json={"exaggeration": 0.7, "seed": 3}).json()[
            "voice"]["exaggeration"] == 0.7

        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()
        assert wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])[
            "status"] == "succeeded"
        plan = client.get(f"/api/episodes/{ep['id']}/voices").json()
        assert plan["missing_samples"] == ["bia"]
        assert [(ln["scene"], ln["voice"]) for ln in plan["lines"]] == [(0, "narrador"), (1, "narrador"), (3, "joao")]

        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/voices", json={}).json()["job_id"])
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        res = job["result"]
        assert res["generated"] == 3 and res["cached"] == 0 and not res["errors"]
        assert res["missing_samples"] == ["bia"] and res["tracks"] == ["joao", "narrador"]

        scenes = client.get(f"/api/episodes/{ep['id']}/scenes").json()
        for a, b in zip(scenes, scenes[1:]):
            assert a["end"] == pytest.approx(b["start"])                 # sem lacunas
        joao = scenes[3]
        speech = 0.25 * len("Calma. Fala baixo, por favor.".split())
        assert joao["end"] - joao["start"] >= speech + voices.LEAD_S + voices.TAIL_S - 0.01
        spec = client.get(f"/api/episodes/{ep['id']}").json()["spec"]
        assert spec["voices"] == {"joao": "ep01/voices/joao.wav", "narrador": "ep01/voices/narrador.wav"}
        clip = next(c for c in spec["clips"] if c["voice"] == "joao")
        assert joao["start"] < clip["at"] < joao["end"]
        assert next(c for c in spec["clips"] if c["voice"] == "narrador")["acoustic"] is None
        assert spec["subs"][-1]["text"] == ["CALMA. FALA BAIXO, POR", "FAVOR."]
        assert spec["total"] == pytest.approx(scenes[-1]["end"])

        # segunda vez: nada muda, nada é refeito
        res2 = wait_job(client, client.post(f"/api/episodes/{ep['id']}/voices", json={}).json()["job_id"])["result"]
        assert res2["generated"] == 0 and res2["cached"] == 3

        # a prévia continua renderizando com as vozes no spec
        fix = client.post(f"/api/scenes/{joao['id']}/regenerate", json={}).json()
        assert wait_job(client, fix["job_id"])["status"] == "succeeded"


def test_voice_job_explains_missing_setup(monkeypatch):
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "nv", "title": "NV"}).json()["id"]
        ch = client.post(f"/api/series/{sid}/characters", json={"slug": "joao", "name": "João"}).json()
        client.post(f"/api/characters/{ch['id']}/voice-sample", files={"file": ("j.wav", _wav_bytes(), "audio/wav")})
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": "João: Oi."}).json()
        wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])
        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/voices", json={}).json()["job_id"])
        assert job["status"] == "failed" and "LOFI_CHATTERBOX_PYTHON" in job["error"]
