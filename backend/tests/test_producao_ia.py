import io
import sys
from pathlib import Path

import numpy as np
from conftest import FakeComfy, figure_png
from fastapi.testclient import TestClient
from PIL import Image

from lofi_studio import compositor, config, pipeline, style_master
from lofi_studio.api import app
from lofi_studio.comfyui import load, registry
from lofi_studio.comfyui.dependencies import check
from test_api import WORKFLOWS, wait_job
from test_video import _make
from test_voices import FAKE_WORKER, _wav_bytes

SCRIPT = """CENA 1 · RUA
João anda pela rua com a lanterna.
Bia corre até João.
BIA (com medo): Eles estão vindo!
JOÃO: Fica atrás de mim.
"""


def test_gritty_preset_and_styled_workflows():
    p = style_master.get("gritty_comic_2d")
    assert "ink outlines" in p.short_prompt and p.generation["height"] == 1344
    info = FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"]).object_info()
    for n in (0, 1, 2):
        wf = load(WORKFLOWS / f"scene_styled_{n}.json")
        assert check(wf, info)["compatible"], n
        style = next(v for v in wf.graph.values() if v["inputs"].get("weight_type") == "style transfer")
        assert style["inputs"]["image"] == ["20", 0]
    two = load(WORKFLOWS / "scene_styled_2.json")
    assert {"mask_a", "mask_b", "reference_a", "reference_b", "style"} <= two.placeholders()


def test_side_masks(tmp_path):
    a, b = pipeline.side_masks(tmp_path)
    left, right = np.asarray(Image.open(a))[..., 0], np.asarray(Image.open(b))[..., 0]
    assert left.shape == (1344, 768)
    assert left[:, 50].min() == 255 and left[:, 718].max() == 0
    assert right[:, 718].min() == 255 and right[:, 50].max() == 0


def test_produce_episode_needs_style_reference():
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "ns", "title": "NS"}).json()["id"]
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": "João anda."}).json()
        r = client.post(f"/api/episodes/{ep['id']}/produce", json={})
        assert r.status_code == 409 and "estilo" in r.json()["detail"]


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (640, 360), color).save(buf, "PNG")
    return buf.getvalue()


def test_produce_episode_one_click(fake_registry, monkeypatch, tmp_path):
    monkeypatch.setenv("LOFI_COMFYUI_WEBSOCKET", "false")
    monkeypatch.setenv("LOFI_CHATTERBOX_PYTHON", sys.executable)
    monkeypatch.setenv("LOFI_CHATTERBOX_WORKER", str(FAKE_WORKER))
    config.get_settings.cache_clear()
    clip = _make(tmp_path / "wan.mp4", "blue", 2, "704x1280", 24, False)
    fake = fake_registry.add_fake("gpu", FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"],
                                                   image_bytes=figure_png(), video_bytes=clip.read_bytes()))
    monkeypatch.setattr(registry, "_registry", fake_registry)
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "gr", "title": "GR", "style_preset": "gritty_comic_2d"}).json()["id"]
        s = client.post(f"/api/series/{sid}/style-reference", files={"file": ("estilo.webp", _png((60, 50, 90)), "image/png")}).json()
        assert s["style_reference"]
        client.patch(f"/api/series/{sid}", json={"production": {"modo": "ia", "qualidade": "alta", "selo": False}})
        for slug, name, pron in (("joao", "João", "ele"), ("bia", "Bia", "ela")):
            ch = client.post(f"/api/series/{sid}/characters", json={
                "slug": slug, "name": name, "pronoun": pron, "description": f"{name}, survivor"}).json()
            client.post(f"/api/characters/{ch['id']}/reference", files={"file": ("r.png", figure_png(), "image/png")})
            client.post(f"/api/characters/{ch['id']}/voice-sample", files={"file": ("v.wav", _wav_bytes(), "audio/wav")})
        client.post(f"/api/series/{sid}/environments", json={"slug": "rua", "name": "rua",
                                                              "description": "flooded street, rain", "motor_scene": "rua"})
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()

        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/produce", json={}).json()["job_id"], timeout=900)
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        r = job["result"]
        assert r["vozes"]["generated"] == 2 and not r["cenas e animação"]["errors"]
        assert len(r["cenas e animação"]["clips"]) == 4                     # todas as cenas animadas

        graphs = list(fake.prompts.values())
        styled = [g for g in graphs if g.get("7", {}).get("inputs", {}).get("filename_prefix", "").startswith("lofi/scene_styled")]
        kinds = {g["7"]["inputs"]["filename_prefix"] for g in styled}
        assert "lofi/scene_styled_2" in kinds                                 # Bia e João juntos, cada um num lado
        duo = next(g for g in styled if g["7"]["inputs"]["filename_prefix"] == "lofi/scene_styled_2")
        assert "LEFT:" in duo["2"]["inputs"]["text"] and "RIGHT:" in duo["2"]["inputs"]["text"]
        assert duo["32"]["inputs"]["image"] != duo["42"]["inputs"]["image"]   # máscaras diferentes
        wan = [g for g in graphs if g.get("11", {}).get("class_type") == "Wan22ImageToVideoLatent"]
        assert wan and all((g["11"]["inputs"]["width"], g["11"]["inputs"]["height"]) == (704, 1280) for g in wan)

        e = client.get(f"/api/episodes/{ep['id']}").json()
        assert e["status"] == "done" and "episode" not in e["spec"] and e["spec"]["subs"]   # sem selo, com legenda
        info = compositor.probe(Path(e["output_path"]))
        assert (info.width, info.height) == (1080, 1920) and info.has_audio           # vertical 9:16 com voz
