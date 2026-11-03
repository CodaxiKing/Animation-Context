import io

import numpy as np
from conftest import FakeComfy, figure_png
from fastapi.testclient import TestClient
from PIL import Image

from lofi_studio import config, face
from lofi_studio.api import app
from lofi_studio.comfyui import load, registry
from lofi_studio.comfyui.dependencies import check
from test_api import WORKFLOWS, wait_job

SCRIPT = "CENA 1 · CORREDOR\nRoberta: Tem alguém aí?\nRoberta escuta passos."


def test_head_found_and_masks_stay_inside(tmp_path):
    p = tmp_path / "pose.png"
    p.write_bytes(figure_png())
    head = face.find_head(p)
    assert head and abs(head.cx - 208) < 8 and abs(head.cy - 112) < 8 and 38 < head.r < 56
    for part in ("eyes", "mouth"):
        m = np.asarray(face.face_mask((416, 608), head, part, "34")) > 0
        ys, xs = np.nonzero(m)
        dist = np.hypot(xs - head.cx, ys - head.cy)
        assert m.sum() > 200 and dist.max() <= head.r * face.INSIDE + 1   # nunca encosta no contorno
    eyes = np.asarray(face.face_mask((416, 608), head, "eyes"))
    mouth = np.asarray(face.face_mask((416, 608), head, "mouth"))
    assert np.nonzero(eyes)[0].mean() < np.nonzero(mouth)[0].mean()       # olhos acima da boca


def test_no_head_returns_none(tmp_path):
    p = tmp_path / "blob.png"
    Image.new("RGB", (200, 300), (200, 200, 202)).save(p)
    assert face.find_head(p) is None


def test_face_workflow_uses_only_core_nodes():
    wf = load(WORKFLOWS / "face_inpaint.json")
    fake = FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"])
    assert check(wf, fake.object_info())["compatible"]
    graph, _ = wf.render({"prompt": "p", "negative": "n", "image": "a.png", "mask": "m.png"}, seed=1)
    assert graph["14"]["inputs"]["destination"] == ["10", 0]                # composição preserva o original


def _png(color=(180, 180, 185)):
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), color).save(buf, "PNG")
    return buf.getvalue()


def test_keyframes_make_blink_and_mouth_variants(fake_registry, monkeypatch):
    monkeypatch.setenv("LOFI_COMFYUI_WEBSOCKET", "false")
    config.get_settings.cache_clear()
    fake = fake_registry.add_fake("gpu", FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"],
                                                   image_bytes=figure_png()))
    monkeypatch.setattr(registry, "_registry", fake_registry)
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "fc", "title": "FC"}).json()["id"]
        ch = client.post(f"/api/series/{sid}/characters", json={"slug": "roberta", "name": "Roberta"}).json()
        client.post(f"/api/characters/{ch['id']}/reference", files={"file": ("r.png", _png(), "image/png")})
        client.post(f"/api/series/{sid}/environments", json={"slug": "corredor", "name": "corredor",
                                                              "motor_scene": "corredor"})
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()
        wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])
        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/keyframes", json={}).json()["job_id"])
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        res = job["result"]
        assert not res["errors"], res["errors"]
        poses = [n for n in res["generated"] if n.startswith("roberta_")]
        assert poses
        assert sorted(res["face_variants"]) == sorted(p + s for p in poses for s in ("_piscando", "_falando"))

        folder = config.get_settings().data_dir / "series" / "fc" / "imagens"
        base = Image.open(folder / f"{poses[0]}.png")
        assert Image.open(folder / f"{poses[0]}_falando.png").size == base.size   # mesmo recorte: sem pulo

        inpaints = [g for g in fake.prompts.values() if "14" in g]
        assert len(inpaints) == 2 * len(poses)
        assert any("eyes closed" in g["2"]["inputs"]["text"] for g in inpaints)
        assert all(g["11"]["inputs"]["image"].startswith("lofi/") for g in inpaints)

        # rodar de novo não refaz nada
        again = wait_job(client, client.post(f"/api/episodes/{ep['id']}/keyframes", json={}).json()["job_id"])
        assert again["result"]["generated"] == [] and again["result"]["face_variants"] == []
