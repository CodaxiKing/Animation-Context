"""Produz um episódio de ponta a ponta a partir de um arquivo de produção.

    python -m lofi_studio.produzir ../examples/producao/producao.json            (API embutida)
    python -m lofi_studio.produzir producao.json --api http://127.0.0.1:8000     (servidor já rodando)

Etapas (cada uma pode ser ligada/desligada em "steps"):
  série e elenco -> referência e voz de cada personagem -> planejar cenas -> imagens-chave (poses,
  fundos, olhos e boca) -> vozes -> clipes Wan 2.2 -> exportar MP4.

Pode ser rodado de novo: o que já existe (série, personagens, imagens, falas) é reaproveitado.
Caminhos relativos no arquivo são relativos à pasta dele.
"""
import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_STEPS = {"plan": True, "character_sheets": False, "keyframes": True, "voices": True,
                 "clips": "action", "harmonize": False, "export": True, "allow_unapproved": True}


class Falha(RuntimeError):
    pass


class Produtor:
    def __init__(self, client, base: Path, log=print):
        self.c, self.base, self.log = client, base, log

    def _ok(self, r, expected=(200, 201, 202)):
        if r.status_code not in expected:
            raise Falha(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:400]}")
        return r.json()

    def wait(self, job_id: int, label: str, timeout: float = 6 * 3600) -> dict:
        last, deadline = "", time.time() + timeout
        while time.time() < deadline:
            job = self._ok(self.c.get(f"/api/jobs/{job_id}"))
            msg = f"  {label}: {int(job['progress'] * 100):3d}% {job['message']}"
            if msg != last:
                self.log(msg)
                last = msg
            if job["status"] == "succeeded":
                return job["result"]
            if job["status"] in ("failed", "cancelled"):
                raise Falha(f"{label} {job['status']}: {job['error']}")
            time.sleep(1.0)
        raise Falha(f"{label}: tempo esgotado")

    def file(self, rel: str) -> Path:
        p = (self.base / rel).resolve()
        if not p.is_file():
            raise Falha(f"arquivo não encontrado: {p}")
        return p

    # --- etapas -----------------------------------------------------------------------------
    def series(self, cfg: dict) -> dict:
        existing = {s["slug"]: s for s in self._ok(self.c.get("/api/series"))}
        if cfg.get("import_motor"):
            slug = cfg["import_motor"]
            if slug in existing:
                return existing[slug]
            self.log(f"importando a série {slug} do motor-lofi")
            return self._ok(self.c.post(f"/api/motor/series/{slug}/import"))
        if cfg["slug"] in existing:
            return existing[cfg["slug"]]
        self.log(f"criando a série {cfg['title']}")
        return self._ok(self.c.post("/api/series", json=cfg))

    def characters(self, sid: int, items: list[dict], sheets: bool) -> dict:
        have = {c["slug"]: c for c in self._ok(self.c.get(f"/api/series/{sid}/characters"))}
        for ch in items:
            body = {k: ch[k] for k in ("slug", "name", "description", "rig", "locked") if k in ch}
            cur = have.get(ch["slug"]) or self._ok(self.c.post(f"/api/series/{sid}/characters", json=body))
            if ch.get("reference") and not cur["reference_assets"]:
                self.log(f"  referência de {ch['slug']}")
                path = self.file(ch["reference"])
                cur = self._ok(self.c.post(f"/api/characters/{cur['id']}/reference",
                                           files={"file": (path.name, path.read_bytes(), "image/png")}))
            elif sheets and not cur["reference_assets"]:
                self.wait(self._ok(self.c.post(f"/api/characters/{cur['id']}/generate-sheet", json={}))["job_id"],
                          f"folha de {ch['slug']}")
            if ch.get("voice_sample") and not cur.get("voice", {}).get("sample_asset"):
                self.log(f"  amostra de voz de {ch['slug']}")
                path = self.file(ch["voice_sample"])
                cur = self._ok(self.c.post(f"/api/characters/{cur['id']}/voice-sample",
                                           files={"file": (path.name, path.read_bytes(), "audio/wav")}))
            if ch.get("voice"):
                cur = self._ok(self.c.put(f"/api/characters/{cur['id']}/voice", json=ch["voice"]))
            have[ch["slug"]] = cur
        return have

    def environments(self, sid: int, items: list[dict]) -> None:
        have = {e["slug"] for e in self._ok(self.c.get(f"/api/series/{sid}/environments"))}
        for env in items:
            if env["slug"] not in have:
                self._ok(self.c.post(f"/api/series/{sid}/environments", json=env))

    def episode(self, sid: int, cfg: dict) -> dict:
        eps = {e["number"]: e for e in self._ok(self.c.get(f"/api/series/{sid}/episodes"))}
        if cfg["number"] in eps:
            return eps[cfg["number"]]
        script = cfg.get("script") or self.file(cfg["script_file"]).read_text(encoding="utf-8")
        return self._ok(self.c.post(f"/api/series/{sid}/episodes",
                                    json={"number": cfg["number"], "title": cfg.get("title", ""), "script": script}))

    def run(self, cfg: dict) -> dict:
        steps = {**DEFAULT_STEPS, **cfg.get("steps", {})}
        report = {}
        series = self.series(cfg["series"])
        sid = series["id"]
        self.characters(sid, cfg.get("characters", []), steps["character_sheets"])
        self.environments(sid, cfg.get("environments", []))
        ep = self.episode(sid, cfg["episode"])
        eid = ep["id"]
        self.log(f"episódio {ep['number']} (id {eid})")
        if steps["plan"]:
            report["plan"] = self.wait(self._ok(self.c.post(f"/api/episodes/{eid}/generate", json={}))["job_id"],
                                       "planejar cenas")
        if steps["keyframes"]:
            report["keyframes"] = self.wait(self._ok(self.c.post(f"/api/episodes/{eid}/keyframes",
                                                                 json=steps.get("keyframes_params", {})))["job_id"],
                                            "imagens-chave")
            if report["keyframes"]["errors"]:
                self.log(f"  avisos nas imagens: {report['keyframes']['errors']}")
        if steps["voices"]:
            try:
                report["voices"] = self.wait(self._ok(self.c.post(f"/api/episodes/{eid}/voices", json={}))["job_id"],
                                             "vozes")
            except Falha as e:   # sem Chatterbox ou sem amostras: segue sem vozes
                self.log(f"  vozes puladas: {e}")
        if steps["clips"]:
            body = {"mode": steps["clips"]} if isinstance(steps["clips"], str) else {"scenes": steps["clips"]}
            body["harmonize"] = steps["harmonize"]
            report["clips"] = self.wait(self._ok(self.c.post(f"/api/episodes/{eid}/video-clips", json=body))["job_id"],
                                        "clipes Wan 2.2")
            if report["clips"]["errors"]:
                self.log(f"  avisos nos clipes: {report['clips']['errors']}")
        if steps["export"]:
            report["export"] = self.wait(self._ok(self.c.post(
                f"/api/episodes/{eid}/export", json={"allow_unapproved": steps["allow_unapproved"]}))["job_id"],
                "exportar")
            self.log(f"vídeo: {report['export']['video']}")
        return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Produz um episódio de ponta a ponta")
    ap.add_argument("config")
    ap.add_argument("--api", help="URL de uma API já rodando (padrão: API embutida neste processo)")
    args = ap.parse_args(argv)
    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if args.api:
        import httpx
        client = httpx.Client(base_url=args.api, timeout=120)
    else:
        from fastapi.testclient import TestClient
        from .api import app
        client = TestClient(app).__enter__()
    try:
        Produtor(client, cfg_path.parent).run(cfg)
    except Falha as e:
        print(f"PAROU: {e}", file=sys.stderr)
        return 1
    finally:
        if not args.api:
            client.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
