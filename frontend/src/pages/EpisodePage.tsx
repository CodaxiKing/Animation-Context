import { useState } from "react";
import { api, Episode, Job, previewUrl, QcCheck, Scene, videoUrl } from "../api";
import { useJob, useLoad } from "../hooks";
import { ActionButton, Badge, Empty, ErrorNote, JobBar, KIND_LABEL, seconds } from "../ui";

export function EpisodePage({ id }: { id: number }) {
  const ep = useLoad(() => api.episode(id), [id]);
  const scenes = useLoad(() => api.scenes(id), [id]);
  const kf = useLoad(() => api.keyframePlan(id).catch(() => null), [id]);
  const vp = useLoad(() => api.voicePlan(id).catch(() => null), [id]);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const refresh = () => { void ep.reload(); void scenes.reload(); void kf.reload(); void vp.reload(); };
  const job = useJob(refresh);

  if (ep.error) return <div className="page"><ErrorNote error={ep.error} /></div>;
  if (!ep.data) return <div className="page muted">carregando…</div>;
  const e = ep.data;
  const list = scenes.data ?? [];
  const approved = list.filter((s) => s.status === "approved").length;
  const start = async (p: Promise<{ job_id: number }>) => job.track((await p).job_id);

  return (
    <div className="page">
      <header className="page-head">
        <p className="crumbs"><a href="#/">Séries</a> / <a href={`#/serie/${e.series_id}`}>série</a></p>
        <h1>{e.title || `Episódio ${e.number}`} <Badge status={e.status} /></h1>
        <p className="muted">
          {list.length} cenas · {e.spec?.total ? seconds(e.spec.total) : "—"} · {approved}/{list.length} aprovadas
        </p>
      </header>

      <Script episode={e} onSaved={(x) => ep.setData(x)} />

      <section className="steps">
        <Step n={1} title="Planejar cenas" hint="roteiro → planos, quem fala, quem está em cena, ações">
          <ActionButton kind="primary" disabled={job.running} onClick={() => start(api.generate(id))}>
            {list.length ? "Planejar de novo" : "Planejar"}
          </ActionButton>
        </Step>
        <Step n={2} title="Imagens-chave" hint={kf.data ? `${kf.data.missing.length} de ${Object.keys(kf.data.images).length} imagens faltando` : "fundos, poses, olhos e boca"}>
          <ActionButton disabled={job.running || !list.length} onClick={() => start(api.keyframes(id))}>Gerar</ActionButton>
        </Step>
        <Step n={3} title="Vozes" hint={vp.data ? `${vp.data.lines.length} falas${vp.data.missing_samples.length ? ` · sem amostra: ${vp.data.missing_samples.join(", ")}` : ""}` : "Chatterbox"}>
          <ActionButton disabled={job.running || !list.length} onClick={() => start(api.voices(id))}>Gerar</ActionButton>
        </Step>
        <ClipStep disabled={job.running || !list.length} selected={[...selected]} onStart={(b) => start(api.clips(id, b))} />
        <ExportStep disabled={job.running || !list.length} onStart={(allow) => start(api.exportEpisode(id, allow))} />
      </section>

      <JobBar job={job.job} onCancel={job.job ? async () => { await api.cancelJob(job.job!.id); } : undefined} />
      {job.job?.status === "succeeded" && <JobSummary job={job.job} />}

      {e.output_path && e.status === "done" && (
        <section className="panel">
          <h2>Vídeo final</h2>
          <video className="final" controls src={videoUrl(e)} />
        </section>
      )}

      {!!e.quality?.findings?.length && (
        <details className="panel">
          <summary>Qualidade ({e.quality.findings.length} observações)</summary>
          <ul className="findings">
            {e.quality.findings.map((f, i) => <li key={i}><Badge status={f.severity === "error" ? "failed" : f.severity === "warning" ? "aviso" : "queued"} label={f.severity} /> {f.message}</li>)}
          </ul>
        </details>
      )}

      <ImageQc episodeId={id} />

      <h2 className="section-title">Cenas</h2>
      <ErrorNote error={scenes.error} />
      {!list.length && <Empty>Planeje o episódio para ver as cenas.</Empty>}
      <div className="scenes">
        {list.map((s) => (
          <SceneCard key={s.id} s={s} checked={selected.has(s.index)}
            onCheck={(on) => {
              const next = new Set(selected);
              if (on) next.add(s.index); else next.delete(s.index);
              setSelected(next);
            }}
            onChange={(x) => scenes.setData(list.map((y) => (y.id === x.id ? x : y)))}
            onJob={(jobId) => job.track(jobId)} />
        ))}
      </div>
    </div>
  );
}

function Step({ n, title, hint, children }: { n: number; title: string; hint: string; children: React.ReactNode }) {
  return (
    <div className="step">
      <span className="step-n">{n}</span>
      <div className="step-body">
        <strong>{title}</strong>
        <span className="muted small">{hint}</span>
        <div className="row wrap">{children}</div>
      </div>
    </div>
  );
}

function ClipStep({ disabled, selected, onStart }: {
  disabled: boolean; selected: number[]; onStart: (b: Record<string, unknown>) => Promise<unknown>;
}) {
  const [mode, setMode] = useState("action");
  const [startFrame, setStartFrame] = useState("motor");
  const [harmonize, setHarmonize] = useState(false);
  return (
    <Step n={4} title="Clipes Wan 2.2" hint="anima as cenas a partir do quadro inicial">
      <select aria-label="Quais cenas" value={mode} onChange={(e) => setMode(e.target.value)}>
        <option value="action">só cenas de ação</option>
        <option value="all">episódio inteiro</option>
        <option value="list">cenas marcadas ({selected.length})</option>
      </select>
      <select aria-label="Quadro inicial" value={startFrame} onChange={(e) => setStartFrame(e.target.value)}>
        <option value="motor">quadro do motor</option>
        <option value="generated">cena desenhada inteira</option>
      </select>
      <label className="check"><input type="checkbox" checked={harmonize} onChange={(e) => setHarmonize(e.target.checked)} /> uniformizar</label>
      <ActionButton disabled={disabled || (mode === "list" && !selected.length)} onClick={() => onStart(
        mode === "list" ? { scenes: selected, start_frame: startFrame, harmonize } : { mode, start_frame: startFrame, harmonize })}>
        Gerar
      </ActionButton>
    </Step>
  );
}

function ExportStep({ disabled, onStart }: { disabled: boolean; onStart: (allow: boolean) => Promise<unknown> }) {
  const [allow, setAllow] = useState(false);
  return (
    <Step n={5} title="Exportar" hint="MP4 com vozes, legendas e clipes">
      <label className="check"><input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} /> sem aprovar todas</label>
      <ActionButton kind="primary" disabled={disabled} onClick={() => onStart(allow)}>Exportar</ActionButton>
    </Step>
  );
}

function JobSummary({ job }: { job: Job }) {
  const r = job.result as Record<string, unknown>;
  const errors = r.errors as Record<string, string> | undefined;
  const items: string[] = [];
  if (Array.isArray(r.generated)) items.push(`${r.generated.length} imagens geradas`);
  if (Array.isArray(r.face_variants)) items.push(`${r.face_variants.length} variantes de olhos/boca`);
  if (typeof r.cached === "number") items.push(`${r.generated} falas geradas, ${r.cached} reaproveitadas`);
  if (r.clips && typeof r.clips === "object") items.push(`${Object.keys(r.clips).length} clipes`);
  if (Array.isArray(r.qc) && r.qc.length) items.push(`${r.qc.length} imagens para revisar (qualidade)`);
  if (typeof r.scenes === "number") items.push(`${r.scenes} cenas`);
  return (
    <div className="summary">
      <strong>{KIND_LABEL[job.kind] ?? job.kind}:</strong> {items.join(" · ") || "concluída"}
      {errors && Object.keys(errors).length > 0 && (
        <ul className="findings">{Object.entries(errors).map(([k, v]) => <li key={k}><code>{k}</code> {v}</li>)}</ul>
      )}
    </div>
  );
}

function Script({ episode, onSaved }: { episode: Episode; onSaved: (e: Episode) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(episode.script);
  const [title, setTitle] = useState(episode.title);
  return (
    <details className="panel" open={open} onToggle={(ev) => setOpen((ev.target as HTMLDetailsElement).open)}>
      <summary>Roteiro</summary>
      <label className="field">Título<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <textarea className="mono" rows={14} value={text} onChange={(e) => setText(e.target.value)} aria-label="Roteiro" />
      <div className="row">
        <ActionButton kind="primary" disabled={text === episode.script && title === episode.title}
          onClick={async () => onSaved(await api.updateEpisode(episode.id, { script: text, title }))}>Salvar</ActionButton>
        <span className="muted small">Depois de salvar, planeje de novo para refazer as cenas.</span>
      </div>
    </details>
  );
}

function SceneCard({ s, checked, onCheck, onChange, onJob }: {
  s: Scene; checked: boolean; onCheck: (on: boolean) => void; onChange: (s: Scene) => void; onJob: (id: number) => void;
}) {
  const [note, setNote] = useState(s.review_notes);
  const [editing, setEditing] = useState(false);
  const n = s.shot.director_notes ?? {};
  const cast = (s.shot.actors ?? []).map((a) => a.char);
  return (
    <article className={`scene status-${s.status}`}>
      <div className="scene-img">
        {s.preview_path ? <img src={previewUrl(s)} alt={`Prévia da cena ${s.index}`} loading="lazy" /> : <span className="muted small">sem prévia</span>}
        {s.shot.video_clip && <span className="chip chip-clip">clipe{s.shot.video_parts && s.shot.video_parts > 1 ? ` ×${s.shot.video_parts}` : ""}</span>}
      </div>
      <div className="scene-body">
        <div className="row between">
          <label className="check"><input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} /> <strong>#{s.index}</strong></label>
          <Badge status={s.status} />
        </div>
        <p className="muted small">{seconds(s.start)}–{seconds(s.end)} · {s.environment ?? "—"}</p>
        <p className="scene-text">{s.text}</p>
        {n.speaker && <p className="small">🗣 <strong>{n.speaker}</strong></p>}
        {cast.length > 0 && <p className="small muted">em cena: {cast.join(", ")}</p>}
        {!!n.actions?.length && (
          <div className="row wrap">{n.actions.map((a, i) => <span key={i} className={`chip ${a.moving ? "chip-move" : ""}`} title={a.motion}>{a.key}</span>)}</div>
        )}
        {s.review_notes && !editing && <p className="note small">✎ {s.review_notes}</p>}
        {editing && (
          <div className="fix">
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="O que corrigir nesta cena?" />
            <ActionButton onClick={async () => { onChange(await api.reviewScene(s.id, { status: "needs_fix", review_notes: note })); setEditing(false); }}>Salvar nota</ActionButton>
          </div>
        )}
        <div className="row wrap">
          <ActionButton kind="primary" disabled={s.status === "approved"} onClick={async () => onChange(await api.reviewScene(s.id, { status: "approved" }))}>Aprovar</ActionButton>
          <button className="btn btn-secondary" onClick={() => setEditing(!editing)}>Corrigir</button>
          <ActionButton onClick={async () => onJob((await api.regenerateScene(s.id, {})).job_id)}>Refazer prévia</ActionButton>
        </div>
      </div>
    </article>
  );
}

function ImageQc({ episodeId }: { episodeId: number }) {
  const [checks, setChecks] = useState<QcCheck[] | null>(null);
  const flagged = checks?.filter((c) => !c.ok) ?? [];
  return (
    <section className="panel">
      <div className="row between">
        <h2>Qualidade das imagens {checks && (flagged.length
          ? <Badge status="aviso" label={`${flagged.length} para revisar`} />
          : <Badge status="ok" label={`${checks.length} ok`} />)}</h2>
        <ActionButton onClick={async () => setChecks((await api.qc(episodeId)).checks)}>Conferir</ActionButton>
      </div>
      <p className="muted small">Compara cada pose com a referência do personagem: rosto redondo, tom de pele do estilo, cores da roupa e do cabelo, recorte do fundo.</p>
      {flagged.length > 0 && (
        <ul className="findings">
          {flagged.map((c) => <li key={c.image}><code>{c.image}</code>: {c.flags.join("; ")}</li>)}
        </ul>
      )}
    </section>
  );
}
