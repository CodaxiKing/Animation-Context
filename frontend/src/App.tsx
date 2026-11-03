import { api } from "./api";
import { useHashRoute, useLoad } from "./hooks";
import { EpisodePage } from "./pages/EpisodePage";
import { Jobs } from "./pages/Jobs";
import { SeriesDetail } from "./pages/SeriesDetail";
import { SeriesList } from "./pages/SeriesList";
import { System } from "./pages/System";

export function App() {
  const route = useHashRoute();
  const health = useLoad(() => api.health(), []);
  const [page, id] = route;
  const view = page === "serie" && id ? <SeriesDetail id={Number(id)} />
    : page === "episodio" && id ? <EpisodePage id={Number(id)} />
    : page === "tarefas" ? <Jobs />
    : page === "sistema" ? <System />
    : <SeriesList />;
  const active = (p: string) => (page ?? "") === p || (p === "" && (page === "serie" || page === "episodio")) ? "active" : "";

  return (
    <div className="shell">
      <aside className="side">
        <a className="brand" href="#/">
          <span className="brand-mark" aria-hidden>◐</span>
          <span>Motor Lo-Fi <small>V5</small></span>
        </a>
        <nav>
          <a className={active("")} href="#/">Séries</a>
          <a className={active("tarefas")} href="#/tarefas">Tarefas</a>
          <a className={active("sistema")} href="#/sistema">Sistema</a>
        </nav>
        <div className="side-foot small">
          {health.data ? (
            <>
              <span className={`dot ${health.data.motor === true ? "dot-ok" : "dot-bad"}`} /> motor {health.data.motor === true ? `(${health.data.motor_scenes} cenários)` : "indisponível"}<br />
              <span className={`dot ${health.data.ffmpeg ? "dot-ok" : "dot-bad"}`} /> FFmpeg<br />
              <span className="muted">API {health.data.version}</span>
            </>
          ) : health.error ? <span className="error">API fora do ar</span> : <span className="muted">…</span>}
        </div>
      </aside>
      <main>{view}</main>
    </div>
  );
}
