import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { ActionButton, Badge, ErrorNote } from "../ui";

export function System() {
  const pre = useLoad(() => api.preflight(), []);
  const engines = useLoad(() => api.engines(), []);
  const comfy = useLoad(() => api.comfyInstances(), []);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("http://127.0.0.1:8188");

  return (
    <div className="page">
      <header className="page-head">
        <h1>Sistema</h1>
        <p className="muted">O que esta máquina tem e o que falta para produzir.</p>
      </header>

      <section className="panel">
        <div className="row between">
          <h2>Pré-requisitos {pre.data && (pre.data.ready ? <Badge status="ok" label="pronto para produzir" /> : <Badge status="falta" label="faltam itens" />)}</h2>
          <ActionButton onClick={pre.reload}>Conferir de novo</ActionButton>
        </div>
        <ErrorNote error={pre.error} />
        {pre.loading && !pre.data && <p className="muted">conferindo… (consulta o ComfyUI e o Chatterbox)</p>}
        <table className="table">
          <tbody>
            {pre.data?.items.map((i) => (
              <tr key={i.item}>
                <td><Badge status={i.status} /></td>
                <td><strong>{i.item}</strong><br /><span className="muted small">{i.detail}</span></td>
                <td className="small">{i.status !== "ok" && i.fix}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="row between">
          <h2>Instâncias do ComfyUI</h2>
          <ActionButton onClick={async () => { await api.comfyDiscover(); await comfy.reload(); }}>Procurar neste PC</ActionButton>
        </div>
        <ErrorNote error={comfy.error} />
        <table className="table">
          <tbody>
            {comfy.data?.map((c) => (
              <tr key={c.name}>
                <td><Badge status={c.online ? "ok" : "falta"} label={c.online ? "online" : "offline"} /></td>
                <td><strong>{c.name}</strong><br /><code className="small">{c.url}</code></td>
                <td className="small">{c.devices.map((d) => `${d.name} · ${d.vram_free_mb}/${d.vram_total_mb} MB livres`).join(", ") || c.last_error}</td>
                <td className="small">fila: {c.queue_remaining}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row wrap">
          <input aria-label="Nome" placeholder="nome (ex.: gpu2)" value={name} onChange={(e) => setName(e.target.value)} />
          <input aria-label="Endereço" value={url} onChange={(e) => setUrl(e.target.value)} />
          <ActionButton disabled={!name} onClick={async () => { await api.comfyAdd(name, url); setName(""); await comfy.reload(); }}>Adicionar</ActionButton>
        </div>
      </section>

      <section className="panel">
        <h2>Serviços do estúdio</h2>
        <table className="table">
          <tbody>
            {engines.data?.map((e) => (
              <tr key={e.id}>
                <td><Badge status={e.status === "ready" ? "ok" : e.status === "planned" ? "falta" : e.status === "partial" ? "aviso" : "queued"} label={e.status} /></td>
                <td><strong>{e.name}</strong><br /><span className="muted small">{e.provider}</span></td>
                <td className="small">{e.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
