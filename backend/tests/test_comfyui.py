from pathlib import Path

import pytest
from conftest import FakeComfy
from sqlmodel import select

from lofi_studio.comfyui import ComfyRejected, NoCompatibleInstance, WorkflowError, load, parse
from lofi_studio.comfyui.dependencies import check
from lofi_studio.comfyui.orchestrator import Cancelled, run_workflow
from lofi_studio.db import session_scope
from lofi_studio.models import GenerationRecord

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"


def sheet(ckpt="model_a.safetensors"):
    wf = load(WORKFLOWS / "character_sheet.json")
    wf.meta["defaults"]["ckpt"] = ckpt
    return wf


def test_render_substitutes_typed_values_and_seed():
    wf = sheet()
    graph, seed = wf.render({"prompt": "a man", "negative": "bad"}, seed=42)
    assert seed == 42
    assert graph["5"]["inputs"]["seed"] == 42
    assert graph["5"]["inputs"]["steps"] == 30 and isinstance(graph["5"]["inputs"]["cfg"], float)
    assert graph["2"]["inputs"]["text"] == "a man"
    assert "_lofi" not in graph


def test_render_inline_placeholder_and_seed_without_marker():
    wf = parse({"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "style, {{prompt}}!"}},
                "2": {"class_type": "KSampler", "inputs": {"seed": 0}}})
    graph, seed = wf.render({"prompt": "rain"}, seed=7)
    assert graph["1"]["inputs"]["text"] == "style, rain!"
    assert graph["2"]["inputs"]["seed"] == 7


def test_render_reports_missing_params():
    with pytest.raises(WorkflowError, match="negative"):
        sheet().render({"prompt": "x"})


def test_ui_format_is_rejected():
    with pytest.raises(WorkflowError, match="Export \\(API\\)"):
        parse({"nodes": [], "links": []})


def test_dependency_report_lists_missing_nodes_and_models():
    fake = FakeComfy(checkpoints=["other.safetensors"])
    wf = parse({**sheet().graph,
                "8": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": "ip.safetensors"}}})
    wf.graph["1"]["inputs"]["ckpt_name"] = "sd_xl_base_1.0.safetensors"
    rep = check(wf, fake.object_info())
    assert not rep["compatible"]
    assert rep["missing_nodes"][0]["install"]["pack"] == "ComfyUI_IPAdapter_plus"
    assert rep["missing_models"][0]["file"] == "sd_xl_base_1.0.safetensors"
    assert rep["missing_models"][0]["folder"] == "checkpoints"


def test_pick_prefers_compatible_instance_with_shortest_queue(fake_registry):
    fake_registry.add_fake("busy", FakeComfy(queue_len=5))
    fake_registry.add_fake("idle", FakeComfy(queue_len=0))
    fake_registry.add_fake("nomodel", FakeComfy(checkpoints=[]))
    inst, rep = fake_registry.pick(sheet())
    assert inst.name == "idle" and rep["compatible"]


def test_pick_without_compatible_instance_explains_why(fake_registry):
    fake_registry.add_fake("a", FakeComfy(checkpoints=[]))
    with pytest.raises(NoCompatibleInstance) as e:
        fake_registry.pick(sheet())
    assert e.value.reports[0]["missing_models"]


def test_run_workflow_downloads_and_records(fake_registry, tmp_path):
    fake = fake_registry.add_fake("gpu", FakeComfy(polls_until_done=2))
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG fake")
    wf = parse({**sheet().graph, "9": {"class_type": "LoadImage", "inputs": {"image": "$reference"}}})
    wf.meta = sheet().meta
    res = run_workflow(wf, {"prompt": "p", "negative": "n"}, tmp_path / "out", images={"reference": ref},
                       seed=123, registry=fake_registry, use_websocket=False)
    assert res.seed == 123 and res.files[0].is_file()
    assert fake.prompts["p1"]["9"]["inputs"]["image"] == "lofi/ref0.png"
    with session_scope() as s:
        rec = s.exec(select(GenerationRecord)).one()
        assert rec.status == "succeeded" and rec.seed == 123 and rec.prompt_id == "p1"
        assert rec.vram_used_peak_mb and rec.gpu_name == "cuda:0 Fake GPU"
        assert rec.duration_s is not None


def test_run_workflow_retries_execution_errors(fake_registry, tmp_path):
    fake_registry.add_fake("gpu", FakeComfy(fail_times=1))
    res = run_workflow(sheet(), {"prompt": "p", "negative": "n"}, tmp_path, seed=5,
                       registry=fake_registry, use_websocket=False, max_retries=2)
    assert res.files
    with session_scope() as s:
        statuses = [r.status for r in s.exec(select(GenerationRecord).order_by(GenerationRecord.id))]
        seeds = {r.seed for r in s.exec(select(GenerationRecord))}
    assert statuses == ["failed", "succeeded"] and seeds == {5}


def test_rejected_workflow_is_not_retried(fake_registry, tmp_path):
    fake = fake_registry.add_fake("gpu", FakeComfy())
    wf = sheet()
    # a instância acha que tem o nó (cache de object_info), mas o servidor recusa
    fake_registry.object_info(fake_registry.get("gpu"))
    fake.nodes.discard("VAEDecode")
    with pytest.raises(ComfyRejected):
        run_workflow(wf, {"prompt": "p", "negative": "n"}, tmp_path, registry=fake_registry, use_websocket=False)
    assert len(fake.prompts) == 0


def test_cancel_interrupts_running_prompt(fake_registry, tmp_path):
    fake = fake_registry.add_fake("gpu", FakeComfy(polls_until_done=50))
    with pytest.raises(Cancelled):
        run_workflow(sheet(), {"prompt": "p", "negative": "n"}, tmp_path, registry=fake_registry,
                     use_websocket=False, should_cancel=lambda: True)
    assert fake.interrupted == 1
    with session_scope() as s:
        assert s.exec(select(GenerationRecord)).one().status == "cancelled"
