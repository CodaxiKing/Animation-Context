import io
from pathlib import Path

import numpy as np
from conftest import figure_png
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from lofi_studio import config, jobs, qc_visual, style_master
from lofi_studio.api import app
from lofi_studio.story_analysis import parse_directive
from test_api import wait_job

SCRIPT = """CENA 1 · COZINHA
[CARTELA] Sábado | 07:00
[STATUS] CEL 58%
Bia toma um gole de café.
Ela anda até a mesa.
[ALERTA] Permaneça em casa! | Feche portas e janelas.
MARCOS (com medo): Ninguém responde no rádio.
Um zumbi persegue Marcos pela cozinha e ele corre.
Uma explosão quebra a janela.
"""


def test_directives():
    assert parse_directive("[ALERTA] Fique em casa | Feche tudo") == {"alerta": {"titulo": "Fique em casa", "texto": "Feche tudo"}}
    assert parse_directive("[NOTIFICAÇÃO] Família | Pai | Áudio") == {"notificacao": {"app": "Família", "titulo": "Pai", "texto": "Áudio"}}
    assert parse_directive("[STATUS] CEL 58% ; ÁGUA 100% CHEIA") == {"status": {"CEL": [0.58, None], "ÁGUA": [1.0, "CHEIA"]}}
    assert parse_directive("[HORA] 7:05") == {"hora": "07:05"}
    assert parse_directive("[MISSAO]") == {"missao": None}
    assert parse_directive("[TENSAO] 0.5") == {"reticula": 0.5}
    assert parse_directive("Bia corre.") is None


def test_cozy_style_preset():
    p = style_master.get("cozy_daylight_2d")
    prompt = style_master.character_prompt(p, "a woman")["prompt"]
    assert "Cozy slice-of-life" in prompt and "round" in prompt
    assert p.motor_profile == "cinematic_cartoon"


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_series_interface_dialogue_portrait_pronouns_and_effects():
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "ui", "title": "UI", "style_preset": "cozy_daylight_2d"}).json()["id"]
        r = client.patch(f"/api/series/{sid}", json={"interface": {
            "status": [{"rotulo": "ÁGUA", "valor": 1, "texto": "CHEIA"}, {"rotulo": "CEL", "valor": 0.9}],
            "relogio": {"dia": "SÁB", "inicio": "06:20"}, "missao": {"titulo": "Sobreviva 72h"},
            "dialogo": True, "efeitos": True}})
        assert r.status_code == 200
        ids = {}
        for slug, name, pron in (("bia", "Bia", "ela"), ("marcos", "Marcos", "ele")):
            ids[slug] = client.post(f"/api/series/{sid}/characters",
                                    json={"slug": slug, "name": name, "pronoun": pron}).json()["id"]
        client.post(f"/api/characters/{ids['marcos']}/reference", files={"file": ("m.png", figure_png(), "image/png")})
        assert client.post(f"/api/series/{sid}/characters", json={"slug": "x", "name": "X", "pronoun": "elx"}).status_code == 422
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()
        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])
        assert job["status"] == "succeeded", job["error"]

        spec = client.get(f"/api/episodes/{ep['id']}").json()["spec"]
        assert spec["interface"]["missao"]["titulo"] == "Sobreviva 72h" and "dialogo" not in spec["interface"]
        sc = client.get(f"/api/episodes/{ep['id']}/scenes").json()
        shots = [s["shot"] for s in sc]
        assert shots[0]["cartela"] == {"titulo": "Sábado", "sub": "07:00"} and shots[0]["status"] == {"CEL": [0.58, None]}
        assert [a["char"] for a in shots[1]["actors"]] == ["bia"]                 # "Ela anda" -> Bia
        assert shots[2]["alerta"]["titulo"] == "Permaneça em casa!"
        d = shots[2]["dialogo"]
        assert d["nome"] == "MARCOS" and d["texto"] == "Ninguém responde no rádio." and shots[2]["legenda"] is False
        portrait = config.get_settings().data_dir / "series" / "ui" / d["retrato"]
        assert portrait.is_file() and Image.open(portrait).size == (512, 512)
        assert shots[3].get("reticula") == 0.7                                    # perigo + ação
        assert shots[4].get("glitch") is True                                     # impacto
        # as prévias renderizaram com a interface (o motor desenha cartela, alerta, diálogo)
        assert all(s["status"] == "ready" for s in sc)


def test_visual_qc_flags(tmp_path):
    anchor = tmp_path / "anchor.png"
    anchor.write_bytes(figure_png())
    same = tmp_path / "same.png"
    same.write_bytes(figure_png())
    ok = qc_visual.check_pose(same, anchor, "frente", tmp_path)
    assert ok.ok and ok.score is not None and ok.score > 0.9, ok

    # rosto colorido (fora do estilo) e roupa de outra cor
    im = Image.open(io.BytesIO(figure_png())).convert("RGB")
    a = np.asarray(im).copy()
    body = (np.abs(a.astype(int) - (60, 62, 70)).sum(axis=2) < 30)
    legs = (np.abs(a.astype(int) - (40, 42, 50)).sum(axis=2) < 30)
    a[body] = (200, 40, 40)                       # roupa inteira de outra cor
    a[legs] = (40, 90, 200)
    im = Image.fromarray(a)
    w, h = im.size
    cx, cy, r = w * .5, h * .185, h * .078
    ImageDraw.Draw(im).ellipse([cx - r * .6, cy - r * .6, cx + r * .6, cy + r * .6], fill=(230, 150, 90))
    bad = tmp_path / "bad.png"
    im.save(bad)
    res = qc_visual.check_pose(bad, anchor, "frente", tmp_path)
    assert not res.ok
    assert any("pele" in f for f in res.flags) and any("cores" in f for f in res.flags), res.flags

    full = tmp_path / "full.png"           # pose com cenário detalhado atrás (o recorte simples não tira)
    rng = np.random.default_rng(1)
    Image.fromarray(rng.integers(0, 255, (300, 200, 3), dtype=np.uint8)).save(full)
    assert any("fundo" in f for f in qc_visual.check_pose(full, None, "frente", tmp_path).flags)


def test_celery_backend_eager(monkeypatch):
    monkeypatch.setenv("LOFI_QUEUE_BACKEND", "celery")
    config.get_settings.cache_clear()
    from lofi_studio import celery_app
    celery_app.app.conf.task_always_eager = True       # executa no mesmo processo, sem Redis
    calls = []

    @jobs.task("teste_soma")
    def soma(ctx):
        ctx.progress(0.5, "metade")
        calls.append(ctx.params["a"] + ctx.params["b"])
        return {"soma": calls[-1]}

    monkeypatch.setattr(jobs, "_queue", None)
    q = jobs.get_queue()
    assert isinstance(q, jobs.CeleryQueue)
    with TestClient(app) as client:
        job_id = q.submit("teste_soma", {"a": 2, "b": 3})
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "succeeded" and job["result"] == {"soma": 5} and calls == [5]
        # cancelamento pelo banco: pedido antes de rodar
        jid = q._create("teste_soma", {"a": 1, "b": 1}, None, None, None, 0)
        q.cancel(jid)
        assert client.get(f"/api/jobs/{jid}").json()["status"] == "cancelled"
        assert client.post(f"/api/jobs/{jid}/retry").status_code == 202
    jobs._HANDLERS.pop("teste_soma", None)
    jobs._queue = None


def test_preflight_lists_new_workflow():
    from lofi_studio import preflight
    assert "scene_full" in preflight.WORKFLOWS and Path(config.get_settings().workflows_dir / "scene_full.json").is_file()
