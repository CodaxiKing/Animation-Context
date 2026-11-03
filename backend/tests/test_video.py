import subprocess

import numpy as np
import pytest
from conftest import FakeComfy, figure_png
from fastapi.testclient import TestClient

from lofi_studio import compositor, config, motor, pipeline
from lofi_studio.api import app
from lofi_studio.comfyui import load, registry
from lofi_studio.comfyui.dependencies import check
from test_api import WORKFLOWS, wait_job

pytestmark = pytest.mark.skipif(not motor.ffmpeg_available(), reason="FFmpeg indisponível")


def _make(path, color, seconds, size, fps, audio):
    cmd = [motor.ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"color=c={color}:s={size}:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return path


def _frame(path, t, size):
    w, h = size
    raw = subprocess.run([motor.ffmpeg_exe(), "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).reshape(-1, 3).mean(axis=0)


def test_clip_length_follows_wan_rule():
    assert pipeline.clip_length(3.0) == 73 and pipeline.clip_length(0.2) == 17 and pipeline.clip_length(9) == 121
    assert all((pipeline.clip_length(s) - 1) % 4 == 0 for s in (1.1, 2.7, 4.4))


def test_splice_puts_clip_in_its_window_and_keeps_audio(tmp_path):
    base = _make(tmp_path / "base.mp4", "red", 3, "320x568", 30, True)
    clip = _make(tmp_path / "clip.mp4", "blue", 0.5, "160x284", 24, False)   # mais curto: segura o último quadro
    out = compositor.splice(base, [(clip, 1.0, 2.0)], tmp_path / "out.mp4")
    info = compositor.probe(out)
    assert (info.width, info.height) == (320, 568) and info.has_audio and abs(info.duration - 3) < 0.15
    red, blue, red2 = (_frame(out, t, (320, 568)) for t in (0.5, 1.8, 2.6))
    assert red[0] > 150 > red[2] and blue[2] > 150 > blue[0] and red2[0] > 150


def test_wan_workflow_reports_missing_models():
    wf = load(WORKFLOWS / "wan22_i2v.json")
    fake = FakeComfy()
    info = fake.object_info()
    info["UNETLoader"]["input"]["required"]["unet_name"] = [["outro_modelo.safetensors"]]
    rep = check(wf, info)
    assert [m["folder"] for m in rep["missing_models"]] == ["diffusion_models"]
    graph, _ = wf.render({"prompt": "p", "negative": "n", "image": "x.png", "length": 49}, seed=3)
    assert graph["11"]["inputs"]["length"] == 49 and graph["11"]["inputs"]["start_image"] == ["10", 0]


def test_video_clip_job_and_export_with_clip(fake_registry, monkeypatch, tmp_path):
    monkeypatch.setenv("LOFI_COMFYUI_WEBSOCKET", "false")
    config.get_settings.cache_clear()
    clip = _make(tmp_path / "wan.mp4", "blue", 2, "480x832", 24, False)
    fake = fake_registry.add_fake("gpu", FakeComfy(image_bytes=figure_png(), video_bytes=clip.read_bytes()))
    monkeypatch.setattr(registry, "_registry", fake_registry)
    with TestClient(app) as client:
        sid = client.post("/api/series", json={"slug": "wn", "title": "WN"}).json()["id"]
        client.post(f"/api/series/{sid}/characters", json={"slug": "joao", "name": "João"})
        ep = client.post(f"/api/series/{sid}/episodes", json={
            "number": 1, "script": "João sai correndo pela rua.\nJoão para, ofegante."}).json()
        assert wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])[
            "status"] == "succeeded"
        assert client.post(f"/api/episodes/{ep['id']}/video-clips", json={"scenes": [9]}).status_code == 422

        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/video-clips",
                                           json={"scenes": [0], "seconds": 2, "seed": 5}).json()["job_id"])
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        assert list(job["result"]["clips"]) == ["0"] and not job["result"]["errors"]
        graph = next(g for g in fake.prompts.values() if "11" in g)
        assert graph["11"]["inputs"]["length"] == 49 and "runs" in graph["5"]["inputs"]["text"]
        scene0 = client.get(f"/api/episodes/{ep['id']}/scenes").json()[0]
        assert scene0["shot"]["video_clip"] == "ep01/clips/scene000_v1.mp4"

        job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/export",
                                           json={"allow_unapproved": True}).json()["job_id"], timeout=600)
        assert job["status"] == "succeeded", (job["error"], job["result"].get("traceback"))
        assert job["result"]["video_clips"] == 1
        out = client.get(f"/api/episodes/{ep['id']}").json()["output_path"]
        info = compositor.probe(__import__("pathlib").Path(out))
        mid = (scene0["start"] + scene0["end"]) / 2
        px = _frame(__import__("pathlib").Path(out), mid, (info.width, info.height))
        assert px[2] > 150 > px[0]                    # o clipe azul aparece no meio da cena 0
