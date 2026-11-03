import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOFI_DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("LOFI_DATA_DIR", str(tmp_path / "data"))
    from lofi_studio import comfyui, config, db, jobs
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    comfyui.registry._registry = None
    jobs._queue = None
    db.init_db()
    yield
    if jobs._queue:
        jobs._queue.shutdown(wait=True)
        jobs._queue = None
    db.get_engine().dispose()
    db.get_engine.cache_clear()
    config.get_settings.cache_clear()


def _png() -> bytes:
    """Figura escura sobre fundo cinza liso, como a saída de uma pose (dá para recortar)."""
    buf = io.BytesIO()
    im = Image.new("RGB", (64, 96), (200, 200, 202))
    im.paste((40, 40, 50), (22, 10, 42, 90))
    im.save(buf, "PNG")
    return buf.getvalue()


def figure_png(size=(416, 608)) -> bytes:
    """Personagem no estilo da série: corpo escuro, cabeça redonda clara com contorno, olhos e boca."""
    from PIL import ImageDraw
    w, h = size
    im = Image.new("RGB", size, (200, 200, 202))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([w * .36, h * .31, w * .64, h * .74], 20, fill=(60, 62, 70), outline=(20, 20, 24), width=4)
    d.rectangle([w * .4, h * .74, w * .6, h * .97], fill=(40, 42, 50))
    cx, cy, r = w * .5, h * .185, h * .078
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(226, 224, 228), outline=(22, 22, 26), width=5)
    for dx in (-.32, .32):
        d.ellipse([cx + dx * r - 5, cy - 6, cx + dx * r + 5, cy + 6], fill=(10, 10, 10))
    d.line([cx - 10, cy + .45 * r, cx + 10, cy + .45 * r], fill=(10, 10, 10), width=3)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class FakeComfy:
    """Servidor ComfyUI de mentira para httpx.MockTransport."""

    def __init__(self, *, nodes=None, checkpoints=("model_a.safetensors",), polls_until_done=1,
                 fail_times=0, queue_len=0, image_bytes=None, video_bytes=None):
        self.nodes = set(nodes or {"CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage",
                                   "KSampler", "VAEDecode", "SaveImage", "LoadImage", "IPAdapterModelLoader",
                                   "CLIPVisionLoader", "IPAdapterAdvanced", "LoadImageMask", "VAEEncode",
                                   "SetLatentNoiseMask", "ImageCompositeMasked", "UNETLoader", "CLIPLoader",
                                   "VAELoader", "ModelSamplingSD3", "Wan22ImageToVideoLatent", "CreateVideo",
                                   "SaveVideo"})
        self.checkpoints = list(checkpoints)
        self.polls_until_done = polls_until_done
        self.fail_times = fail_times
        self.queue_len = queue_len
        self.image_bytes = image_bytes
        self.video_bytes = video_bytes
        self.prompts: dict[str, dict] = {}
        self.polls: dict[str, int] = {}
        self.uploads: list[str] = []
        self.deleted: list[str] = []
        self.interrupted = 0
        self.vram_free = 6 * 2**30

    def object_info(self):
        info = {n: {"input": {"required": {}}} for n in self.nodes}
        if "CheckpointLoaderSimple" in info:
            info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"] = [self.checkpoints]
        if "IPAdapterModelLoader" in info:
            info["IPAdapterModelLoader"]["input"]["required"]["ipadapter_file"] = [
                ["ip-adapter-plus_sdxl_vit-h.safetensors"]]
        if "CLIPVisionLoader" in info:
            info["CLIPVisionLoader"]["input"]["required"]["clip_name"] = [
                ["CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"]]
        return info

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/system_stats":
            self.vram_free -= 2**28
            return httpx.Response(200, json={"system": {"comfyui_version": "0.3.test"}, "devices": [
                {"name": "cuda:0 Fake GPU", "type": "cuda", "vram_total": 8 * 2**30, "vram_free": self.vram_free}]})
        if path == "/object_info":
            return httpx.Response(200, json=self.object_info())
        if path == "/models":
            return httpx.Response(200, json=["checkpoints", "loras"])
        if path == "/models/checkpoints":
            return httpx.Response(200, json=self.checkpoints)
        if path in ("/models/diffusion_models", "/models/text_encoders", "/models/vae"):
            return httpx.Response(200, json=[])
        if path == "/models/loras":
            return httpx.Response(200, json=[])
        if path == "/queue" and method == "GET":
            running = [[0, pid] for pid in self.prompts if self.polls.get(pid, 0) < self.polls_until_done]
            return httpx.Response(200, json={"queue_running": running[:1],
                                             "queue_pending": [[0, f"x{i}"] for i in range(self.queue_len)]})
        if path == "/queue" and method == "POST":
            self.deleted += json.loads(request.content)["delete"]
            return httpx.Response(200, json={})
        if path == "/interrupt":
            self.interrupted += 1
            return httpx.Response(200, json={})
        if path == "/upload/image":
            name = f"ref{len(self.uploads)}.png"
            self.uploads.append(name)
            return httpx.Response(200, json={"name": name, "subfolder": "lofi", "type": "input"})
        if path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            bad = {k: {"class_type": n["class_type"]} for k, n in graph.items() if n["class_type"] not in self.nodes}
            if bad:
                return httpx.Response(400, json={"error": "invalid", "node_errors": bad})
            pid = f"p{len(self.prompts) + 1}"
            self.prompts[pid] = graph
            return httpx.Response(200, json={"prompt_id": pid, "number": len(self.prompts), "node_errors": {}})
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            self.polls[pid] = self.polls.get(pid, 0) + 1
            if self.polls[pid] < self.polls_until_done:
                return httpx.Response(200, json={})
            if self.fail_times > 0:
                self.fail_times -= 1
                return httpx.Response(200, json={pid: {"outputs": {}, "status": {
                    "status_str": "error", "completed": False,
                    "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}}})
            is_video = any(n["class_type"] == "SaveVideo" for n in self.prompts[pid].values())
            ext = "mp4" if is_video else "png"
            return httpx.Response(200, json={pid: {"outputs": {"7": {"images": [
                {"filename": f"{pid}_00001_.{ext}", "subfolder": "lofi", "type": "output"}]}},
                "status": {"status_str": "success", "completed": True, "messages": []}}})
        if path == "/view":
            if request.url.params["filename"].endswith(".mp4"):
                return httpx.Response(200, content=self.video_bytes or b"", headers={"content-type": "video/mp4"})
            return httpx.Response(200, content=self.image_bytes or _png(), headers={"content-type": "image/png"})
        return httpx.Response(404, json={"error": path})


@pytest.fixture
def fake_registry():
    from lofi_studio.comfyui.client import ComfyClient
    from lofi_studio.comfyui.registry import Registry

    servers: dict[str, FakeComfy] = {}

    def factory(url):
        return ComfyClient(url, transport=httpx.MockTransport(servers[url].handler))

    reg = Registry(client_factory=factory)

    def add(name, server: FakeComfy):
        url = f"http://fake-{name}:8188"
        servers[url] = server
        reg.add(name, url, declared=True)
        return server

    reg.add_fake = add
    return reg
