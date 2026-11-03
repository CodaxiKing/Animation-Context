import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { ActionButton, Empty, ErrorNote } from "../ui";

export function SeriesList() {
  const list = useLoad(() => api.series(), []);
  const motor = useLoad(() => api.motorSeries(), []);
  const styles = useLoad(() => api.styles(), []);
  const [form, setForm] = useState({ slug: "", title: "", genre: "suspense", style_preset: "dark_cinematic_lofi_2d", synopsis: "" });
  const imported = new Set((list.data ?? []).map((s) => s.motor_slug).filter(Boolean));

  return (
    <div className="page">
      <header className="page-head">
        <h1>Séries</h1>
        <p className="muted">Crie uma série ou traga uma que já existe no motor-lofi.</p>
      </header>
      <ErrorNote error={list.error} />
      {list.data && list.data.length === 0 && <Empty>Nenhuma série ainda. Crie a primeira abaixo.</Empty>}
      <div className="cards">
        {list.data?.map((s) => (
          <a key={s.id} className="card card-link" href={`#/serie/${s.id}`}>
            <h3>{s.title}</h3>
            <p className="muted small">{s.genre} · {s.style_preset}</p>
            {s.motor_slug && <p className="small">importada de <code>{s.motor_slug}</code></p>}
          </a>
        ))}
      </div>

      <section className="panel">
        <h2>Nova série</h2>
        <div className="grid2">
          <label className="field">Título<input value={form.title} onChange={(e) => {
            const title = e.target.value;
            const slug = title.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            setForm({ ...form, title, slug });
          }} placeholder="Sinal de Vida" /></label>
          <label className="field">Identificador<input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} /></label>
          <label className="field">Gênero<input value={form.genre} onChange={(e) => setForm({ ...form, genre: e.target.value })} /></label>
          <label className="field">Estilo visual
            <select value={form.style_preset} onChange={(e) => setForm({ ...form, style_preset: e.target.value })}>
              {styles.data?.map((st) => <option key={st.id} value={st.id}>{st.name}</option>)}
            </select>
          </label>
        </div>
        <label className="field">Sinopse<textarea rows={3} value={form.synopsis} onChange={(e) => setForm({ ...form, synopsis: e.target.value })} /></label>
        <ActionButton kind="primary" disabled={!form.slug || !form.title} onClick={async () => {
          const s = await api.createSeries(form);
          window.location.hash = `#/serie/${s.id}`;
        }}>Criar série</ActionButton>
      </section>

      {motor.data && motor.data.length > 0 && (
        <section className="panel">
          <h2>Importar do motor-lofi</h2>
          <p className="muted small">Traz as fichas travadas dos personagens e os lugares do mapa, sem mudar a série original.</p>
          <div className="row wrap">
            {motor.data.map((slug) => (
              <ActionButton key={slug} disabled={imported.has(slug)} onClick={async () => {
                const s = await api.importSeries(slug);
                window.location.hash = `#/serie/${s.id}`;
              }}>{imported.has(slug) ? `${slug} ✓` : slug}</ActionButton>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
