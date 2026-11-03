import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lofi_studio import style_master
from lofi_studio.api import app

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"

SCRIPT = """Roberta acorda no apartamento escuro. A energia caiu.
Ela pega a lanterna e escuta passos no corredor.
Alguém bate na porta três vezes.
Roberta segura a respiração, com medo."""


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.3)
    raise AssertionError(f"tarefa {job_id} não terminou")


def test_health_sees_motor(client):
    data = client.get("/health").json()
    assert data["motor"] is True and data["motor_scenes"] > 10


def test_style_master_uses_motor_master_style():
    preset = style_master.get("dark_cinematic_lofi_2d")
    p = style_master.character_prompt(preset, "A tired delivery worker", view="three_quarter", emotion="fear")
    assert "MASTER CHARACTER STYLE" in p["prompt"] and "three-quarter" in p["prompt"]
    assert "realistic nose" in p["negative"]
    env = style_master.environment_prompt(preset, "abandoned supermarket aisle")
    assert "no characters" in env["prompt"] and "people" in env["negative"]


def test_import_motor_series_keeps_locked_sheets(client):
    r = client.post("/api/motor/series/sinal-de-vida/import")
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    chars = client.get(f"/api/series/{sid}/characters").json()
    joao = next(c for c in chars if c["slug"] == "joao")
    assert joao["locked"] and joao["rig"]["top_style"] == "hoodie"
    envs = client.get(f"/api/series/{sid}/environments").json()
    assert any(e["motor_scene"] == "mercado" for e in envs)
    r = client.patch(f"/api/characters/{joao['id']}", json={"description": "outro visual"})
    assert r.status_code == 423
    r = client.patch(f"/api/characters/{joao['id']}", json={"description": "outro visual", "unlock": True})
    assert r.status_code == 200
    assert client.post("/api/motor/series/sinal-de-vida/import").status_code == 409


def test_episode_flow_plan_preview_fix_review(client):
    sid = client.post("/api/series", json={"slug": "teste", "title": "TESTE"}).json()["id"]
    client.post(f"/api/series/{sid}/characters", json={
        "slug": "roberta", "name": "Roberta", "rig": {"hair": "ponytail", "top_style": "puffer"}})
    client.post(f"/api/series/{sid}/environments", json={
        "slug": "corredor", "name": "corredor", "motor_scene": "corredor"})
    ep = client.post(f"/api/series/{sid}/episodes", json={"number": 1, "title": "A porta", "script": SCRIPT}).json()

    job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/generate", json={}).json()["job_id"])
    assert job["status"] == "succeeded", job["error"] or job["result"]
    assert client.get(f"/api/episodes/{ep['id']}").json()["status"] == "review"

    scenes = client.get(f"/api/episodes/{ep['id']}/scenes").json()
    assert len(scenes) == 4
    assert scenes[1]["environment"] == "corredor" and scenes[1]["shot"]["scene"] == "corredor"
    img = client.get(f"/api/scenes/{scenes[0]['id']}/preview")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"

    fix = client.post(f"/api/scenes/{scenes[2]['id']}/regenerate",
                      json={"shot_patch": {"camera": {"zoom": 1.4}}}).json()
    job = wait_job(client, fix["job_id"])
    assert job["status"] == "succeeded", job["error"]
    sc = client.get(f"/api/episodes/{ep['id']}/scenes").json()[2]
    assert sc["version"] == 2 and sc["shot"]["camera"]["zoom"] == 1.4

    assert client.post(f"/api/scenes/{sc['id']}/regenerate", json={"shot_patch": {"start": 0}}).status_code == 422

    # exportar sem aprovar todas as cenas falha com mensagem clara
    job = wait_job(client, client.post(f"/api/episodes/{ep['id']}/export", json={}).json()["job_id"])
    assert job["status"] == "failed" and "não aprovadas" in job["error"]
    for s in client.get(f"/api/episodes/{ep['id']}/scenes").json():
        assert client.patch(f"/api/scenes/{s['id']}", json={"status": "approved"}).status_code == 200


def test_job_retry_and_unknown_workflow(client):
    sid = client.post("/api/series", json={"slug": "x", "title": "X"}).json()["id"]
    ch = client.post(f"/api/series/{sid}/characters", json={"slug": "ana", "name": "Ana"}).json()
    # sem instância ComfyUI configurada: a tarefa falha explicando o motivo, e pode ser repetida
    job = wait_job(client, client.post(f"/api/characters/{ch['id']}/generate-sheet", json={}).json()["job_id"])
    assert job["status"] == "failed" and "nenhuma instância" in job["error"]
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 202
    assert client.get("/api/comfyui/workflows/../check").status_code in (404, 422)
    names = {w["name"] for w in client.get("/api/comfyui/workflows").json()}
    assert {"character_sheet", "environment"} <= names
