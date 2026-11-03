import io

from conftest import FakeComfy
from fastapi.testclient import TestClient
from PIL import Image

from lofi_studio import config, keyframes
from lofi_studio.api import app
from lofi_studio.comfyui import load, registry
from lofi_studio.comfyui.dependencies import check
from test_api import SCRIPT, WORKFLOWS, wait_job  # noqa: F401  (WORKFLOWS reexportado abaixo)


def test_scene_images_follow_motor_naming():
    shot = {"camera": {"zoom": 1.28}, "actors": [
        {"char": "roberta", "view": "front", "emotion": "fear", "pose": "hide"},
        {"char": "dona_maria", "view": "side", "emotion": "neutral", "walk": True}]}
    cfg = keyframes.scene_images(shot, "corredor")
    assert cfg["fundo"] == "fundo_corredor" and cfg["plano"] == "close"
    assert [p["img"] for p in cfg["personagens"]] == ["roberta_frente_medo_agachado", "dona_maria_lado_neutro_andando"]
    assert cfg["personagens"][1]["andar"]
    req = keyframes.requirements([cfg])
    assert req["dona_maria_lado_neutro_andando"] == {"kind": "character", "char": "dona_maria", "view": "lado",
                                                     "emotion": "neutro", "pose": "andando"}
    assert req["fundo_corredor"]["environment"] == "corredor"


def test_character_sheet_anchor_uses_front_view(tmp_path):
    sheet = Image.new("RGB", (1500, 1000), (200, 200, 200))
    sheet.paste((255, 0, 0), (0, 0, 500, 1000))   # vista de frente (esquerda) em vermelho
    sheet.save(tmp_path / "sheet.png")
    out = keyframes.anchor_for_ipadapter(tmp_path / "sheet.png", tmp_path / "anchor.png")
    im = Image.open(out)
    assert im.size == (1024, 1024) and im.getpixel((512, 512)) == (255, 0, 0)


def test_pose_workflow_reports_missing_ipadapter_models():
    wf = load(WORKFLOWS / "character_pose.json")
    fake = FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"])
    fake_info = fake.object_info()
    fake_info["CLIPVisionLoader"]["input"]["required"]["clip_name"] = [[]]
    rep = check(wf, fake_info)
    assert [m["folder"] for m in rep["missing_models"]] == ["clip_vision"]
    assert check(wf, fake.object_info())["compatible"]


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), (180, 180, 185)).save(buf, "PNG")
    return buf.getvalue()


def test_keyframes_generated_cut_out_and_rendered(fake_registry, monkeypatch):
    monkeypatch.setenv("LOFI_COMFYUI_WEBSOCKET", "false")
    config.get_settings.cache_clear()
    fake = fake_registry.add_fake("gpu", FakeComfy(checkpoints=["sd_xl_base_1.0.safetensors"]))
    monkeypatch.setattr(registry, "_registry", fake_registry)

    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "kf", "title": "KF"}).json()["id"]
        ch = client.post(f"/api/series/{sid}/characters", json={
            "slug": "roberta", "name": "Roberta", "description": "young woman, beanie, puffer jacket"}).json()
        client.post(f"/api/series/{sid}/environments", json={
            "slug": "corredor", "name": "corredor", "description": "dark apartment hallway", "motor_scene": "corredor"})
        ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "script": SCRIPT}).json()
        assert wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])[
            "status"] == "succeeded"

        # sem âncora: as poses falham com explicação e as cenas ficam bloqueadas
        plan = client.get(f"/api/episodes/{ep['id']}/keyframes").json()
        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/keyframes", json={}).json()["job_id"])
        assert any("âncora" in e for e in job["result"]["errors"].values()), job

        r = client.post(f"/api/characters/{ch['id']}/reference", files={"file": ("ref.png", _png_bytes(), "image/png")})
        assert r.status_code == 201 and len(r.json()["reference_assets"]) == 1

        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/keyframes", json={"seed": 9}).json()["job_id"])
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        res = job["result"]
        assert not res["errors"] and not res["scenes_blocked"]
        # os fundos saíram na tarefa anterior; agora só as poses que dependiam da âncora
        assert sorted(res["generated"]) == sorted(n for n in plan["missing"] if n.startswith("roberta_"))
        assert len(res["scenes_ready"]) == 4

        # a pose foi gerada com a âncora enviada ao ComfyUI pelo IPAdapter
        pose_graphs = [g for g in fake.prompts.values() if "13" in g]
        assert pose_graphs and all(g["10"]["inputs"]["image"].startswith("lofi/") for g in pose_graphs)

        scenes = client.get(f"/api/episodes/{ep['id']}/scenes").json()
        assert all("imagens" in sc["shot"] for sc in scenes)
        assert client.get(f"/api/scenes/{scenes[0]['id']}/preview").content[:4] == b"\x89PNG"

        # nada mais falta: o próximo episódio reaproveita a biblioteca da série
        assert client.get(f"/api/episodes/{ep['id']}/keyframes").json()["missing"] == []
        cut = Image.open(next((p for p in (config.get_settings().data_dir / "series" / "kf" / "imagens").glob(
            "roberta_*.png"))))
        assert cut.mode == "RGBA" and cut.size == (20, 80)   # fundo cinza removido e sobras cortadas
