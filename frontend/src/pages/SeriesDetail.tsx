import { useState } from "react";
import { api, assetUrl, Character, Environment, Series, StatusItem } from "../api";
import { useJob, useLoad } from "../hooks";
import { ActionButton, Badge, Empty, ErrorNote, FilePick, JobBar } from "../ui";

type Tab = "elenco" | "cenarios" | "episodios" | "interface";

export function SeriesDetail({ id }: { id: number }) {
  const series = useLoad(async () => (await api.series()).find((s) => s.id === id) ?? null, [id]);
  const [tab, setTab] = useState<Tab>("episodios");
  if (series.error) return <div className="page"><ErrorNote error={series.error} /></div>;
  if (!series.data) return <div className="page muted">carregando…</div>;
  const s = series.data;
  return (
    <div className="page">
      <header className="page-head">
        <p className="crumbs"><a href="#/">Séries</a></p>
        <h1>{s.title}</h1>
        <p className="muted">{s.genre} · estilo {s.style_preset}</p>
      </header>
      <nav className="tabs" role="tablist">
        {(["episodios", "elenco", "cenarios", "interface"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ episodios: "Episódios", elenco: "Elenco", cenarios: "Cenários", interface: "Estilo e interface" }[t]}
          </button>
        ))}
      </nav>
      {tab === "interface" && <InterfaceForm series={s} onSaved={(x) => series.setData(x)} />}
      {tab === "elenco" && <Cast seriesId={id} />}
      {tab === "cenarios" && <Places seriesId={id} />}
      {tab === "episodios" && <Episodes seriesId={id} />}
    </div>
  );
}

function Cast({ seriesId }: { seriesId: number }) {
  const chars = useLoad(() => api.characters(seriesId), [seriesId]);
  const [form, setForm] = useState({ slug: "", name: "", description: "" });
  const replace = (c: Character) => chars.setData((chars.data ?? []).map((x) => (x.id === c.id ? c : x)));
  return (
    <>
      <ErrorNote error={chars.error} />
      {chars.data?.length === 0 && <Empty>Cadastre os personagens. Cada um precisa de uma imagem de referência
        (a cara dele na série inteira) e, para falar, de 6 a 15 s de voz.</Empty>}
      <div className="cards">
        {chars.data?.map((c) => <CharacterCard key={c.id} c={c} onChange={replace} />)}
      </div>
      <section className="panel">
        <h2>Novo personagem</h2>
        <div className="grid2">
          <label className="field">Nome<input value={form.name} onChange={(e) => setForm({
            ...form, name: e.target.value,
            slug: e.target.value.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
          })} /></label>
          <label className="field">Identificador<input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} /></label>
        </div>
        <label className="field">Aparência (em inglês, vai para os prompts)
          <textarea rows={3} value={form.description} placeholder="30-year-old woman, brown hair in a low ponytail, navy zip jacket…"
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
        <p className="muted small">Use <code>narrador</code> como identificador para a voz que lê as linhas sem fala.</p>
        <ActionButton kind="primary" disabled={!form.slug || !form.name} onClick={async () => {
          await api.createCharacter(seriesId, form);
          setForm({ slug: "", name: "", description: "" });
          await chars.reload();
        }}>Adicionar</ActionButton>
      </section>
    </>
  );
}

function CharacterCard({ c, onChange }: { c: Character; onChange: (c: Character) => void }) {
  const sheet = useJob();
  const anchor = c.reference_assets[0];
  return (
    <article className="card char">
      <div className="thumb">{anchor ? <img src={assetUrl(anchor)} alt={`Referência de ${c.name}`} /> : <span className="muted small">sem referência</span>}</div>
      <div className="char-body">
        <h3>{c.name} {c.locked && <span title="ficha travada: o roteiro não muda o visual">🔒</span>}</h3>
        <p className="muted small"><code>{c.slug}</code></p>
        {c.description && <p className="small clamp">{c.description}</p>}
        <label className="small row">pronome
          <select value={c.pronoun ?? ""} onChange={async (e) => onChange(await api.updateCharacter(c.id, { pronoun: e.target.value || null }))}>
            <option value="">—</option><option value="ela">ela</option><option value="ele">ele</option>
          </select>
        </label>
        <p className="small">voz: {c.voice?.sample_asset ? <Badge status="ok" label="amostra ok" /> : <Badge status="aviso" label="sem amostra" />}</p>
        <div className="row wrap">
          <FilePick label="Referência" accept="image/*" onFile={async (f) => onChange(await api.uploadReference(c.id, f))} />
          <FilePick label="Voz" accept=".wav,.mp3,.flac,.ogg" onFile={async (f) => onChange(await api.uploadVoice(c.id, f))} />
          <ActionButton onClick={async () => sheet.track((await api.generateSheet(c.id)).job_id)}>Gerar folha</ActionButton>
        </div>
        <JobBar job={sheet.job} />
      </div>
    </article>
  );
}

function Places({ seriesId }: { seriesId: number }) {
  const envs = useLoad(() => api.environments(seriesId), [seriesId]);
  const scenes = useLoad(() => api.motorScenes(), []);
  const [form, setForm] = useState({ slug: "", name: "", description: "", motor_scene: "" });
  return (
    <>
      <ErrorNote error={envs.error} />
      <table className="table">
        <thead><tr><th>Cenário</th><th>Descrição</th><th>Desenho do motor</th><th /></tr></thead>
        <tbody>
          {envs.data?.map((e) => <PlaceRow key={e.id} e={e} />)}
        </tbody>
      </table>
      <section className="panel">
        <h2>Novo cenário</h2>
        <div className="grid2">
          <label className="field">Nome (como aparece no roteiro)<input value={form.name} onChange={(e) => setForm({
            ...form, name: e.target.value,
            slug: e.target.value.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
          })} /></label>
          <label className="field">Desenho do motor (opcional)
            <select value={form.motor_scene} onChange={(e) => setForm({ ...form, motor_scene: e.target.value })}>
              <option value="">nenhum</option>
              {scenes.data?.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
        <label className="field">Descrição (em inglês, vai para os prompts)
          <textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
        <ActionButton kind="primary" disabled={!form.slug} onClick={async () => {
          await api.createEnvironment(seriesId, { ...form, motor_scene: form.motor_scene || null });
          setForm({ slug: "", name: "", description: "", motor_scene: "" });
          await envs.reload();
        }}>Adicionar</ActionButton>
      </section>
    </>
  );
}

function PlaceRow({ e }: { e: Environment }) {
  const art = useJob();
  return (
    <tr>
      <td><strong>{e.name}</strong><br /><code className="small">{e.slug}</code></td>
      <td className="small">{e.description || <span className="muted">—</span>}</td>
      <td>{e.motor_scene ? <code>{e.motor_scene}</code> : <span className="muted">—</span>}</td>
      <td>
        <ActionButton onClick={async () => art.track((await api.generateEnvironmentArt(e.id)).job_id)}>Gerar arte</ActionButton>
        <JobBar job={art.job} />
      </td>
    </tr>
  );
}

function Episodes({ seriesId }: { seriesId: number }) {
  const eps = useLoad(() => api.episodes(seriesId), [seriesId]);
  const next = (eps.data?.reduce((m, e) => Math.max(m, e.number), 0) ?? 0) + 1;
  const [title, setTitle] = useState("");
  const [script, setScript] = useState("");
  return (
    <>
      <ErrorNote error={eps.error} />
      <table className="table">
        <thead><tr><th>#</th><th>Título</th><th>Situação</th><th>Duração</th></tr></thead>
        <tbody>
          {eps.data?.map((e) => (
            <tr key={e.id} className="clickable" onClick={() => { window.location.hash = `#/episodio/${e.id}`; }}>
              <td>{e.number}</td>
              <td><a href={`#/episodio/${e.id}`}>{e.title || `Episódio ${e.number}`}</a></td>
              <td><Badge status={e.status} /></td>
              <td>{e.spec?.total ? `${Math.round(e.spec.total)} s` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <section className="panel">
        <h2>Novo episódio ({next})</h2>
        <label className="field">Título<input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field">Roteiro
          <textarea rows={12} className="mono" value={script} onChange={(e) => setScript(e.target.value)}
            placeholder={"CENA 1 · COZINHA\nBia toma um gole de café e anda até a mesa.\nBIA (com medo): Ninguém sai daqui.\nMarcos se levanta e vai até a janela."} />
        </label>
        <ActionButton kind="primary" disabled={!script.trim()} onClick={async () => {
          const e = await api.createEpisode(seriesId, { number: next, title, script });
          window.location.hash = `#/episodio/${e.id}`;
        }}>Criar episódio</ActionButton>
      </section>
    </>
  );
}

const statusToText = (items?: StatusItem[]) =>
  (items ?? []).map((i) => `${i.rotulo} ${Math.round(i.valor * 100)}%${i.texto ? " " + i.texto : ""}`).join("; ");

function textToStatus(text: string): StatusItem[] {
  return text.split(/[;\n]/).map((part) => part.trim().match(/^(.+?)\s+(\d{1,3})\s*%\s*(.*)$/))
    .filter((m): m is RegExpMatchArray => m !== null)
    .map((m) => ({ rotulo: m[1].toUpperCase(), valor: Number(m[2]) / 100, texto: m[3] ? m[3].toUpperCase() : null }));
}

function InterfaceForm({ series, onSaved }: { series: Series; onSaved: (s: Series) => void }) {
  const ui = series.interface ?? {};
  const styles = useLoad(() => api.styles(), []);
  const [style, setStyle] = useState(series.style_preset);
  const [enabled, setEnabled] = useState(Boolean(ui.status?.length || ui.relogio || ui.missao));
  const [status, setStatus] = useState(statusToText(ui.status) || "ÁGUA 100% CHEIA; COMIDA 100% CHEIA; CEL 58%");
  const [dia, setDia] = useState(ui.relogio?.dia ?? "SÁB");
  const [inicio, setInicio] = useState(ui.relogio?.inicio ?? "06:20");
  const [missao, setMissao] = useState(ui.missao?.titulo ?? "");
  const [prazo, setPrazo] = useState(ui.missao?.prazo ?? "");
  const [dialogo, setDialogo] = useState(Boolean(ui.dialogo));
  const [efeitos, setEfeitos] = useState(ui.efeitos !== false);
  return (
    <section className="panel">
      <h2>Estilo visual</h2>
      <select value={style} onChange={(e) => setStyle(e.target.value)}>
        {styles.data?.map((st) => <option key={st.id} value={st.id}>{st.name}: {st.description}</option>)}
      </select>
      <h2 className="section-title">Interface de sobrevivência</h2>
      <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> mostrar barras, relógio e missão</label>
      {enabled && (
        <div className="grid2">
          <label className="field">Barras (rótulo, %, texto opcional; separe com ;)
            <input value={status} onChange={(e) => setStatus(e.target.value)} />
          </label>
          <label className="field">Missão (título e prazo)
            <span className="row"><input value={missao} onChange={(e) => setMissao(e.target.value)} placeholder="Sobreviva 72h" />
              <input value={prazo} onChange={(e) => setPrazo(e.target.value)} placeholder="72:00" size={6} /></span>
          </label>
          <label className="field">Relógio (dia e hora inicial)
            <span className="row"><input value={dia} onChange={(e) => setDia(e.target.value)} size={5} />
              <input value={inicio} onChange={(e) => setInicio(e.target.value)} size={6} /></span>
          </label>
        </div>
      )}
      <label className="check"><input type="checkbox" checked={dialogo} onChange={(e) => setDialogo(e.target.checked)} /> falas em caixa de diálogo com retrato (no lugar da legenda)</label><br />
      <label className="check"><input type="checkbox" checked={efeitos} onChange={(e) => setEfeitos(e.target.checked)} /> efeitos de tensão automáticos (retícula em perigo, glitch em impacto)</label>
      <p className="muted small">No roteiro: <code>[ALERTA] título | texto</code>, <code>[NOTIFICACAO] app | título | texto</code>,
        {" "}<code>[CARTELA] Sábado | 07:00</code>, <code>[HORA] 07:00</code>, <code>[STATUS] CEL 20%</code>, <code>[MISSAO] título | prazo</code>,
        {" "}<code>[TENSAO]</code>, <code>[GLITCH]</code>. Vale ao planejar o episódio de novo.</p>
      <ActionButton kind="primary" onClick={async () => onSaved(await api.updateSeries(series.id, {
        style_preset: style,
        interface: {
          ...(enabled ? {
            status: textToStatus(status),
            relogio: { dia, inicio },
            ...(missao ? { missao: { titulo: missao, ...(prazo ? { prazo } : {}) } } : {}),
          } : {}),
          dialogo, efeitos,
        },
      }))}>Salvar</ActionButton>
    </section>
  );
}
