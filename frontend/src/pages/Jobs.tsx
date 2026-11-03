import { useEffect } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { ActionButton, Badge, Empty, ErrorNote, KIND_LABEL } from "../ui";

export function Jobs() {
  const jobs = useLoad(() => api.jobs(), []);
  const active = (jobs.data ?? []).some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void jobs.reload(), 2000);
    return () => window.clearInterval(t);
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <header className="page-head">
        <h1>Tarefas</h1>
        <p className="muted">Tudo o que a fila executou ou está executando.</p>
      </header>
      <ErrorNote error={jobs.error} />
      {jobs.data?.length === 0 && <Empty>Nenhuma tarefa ainda.</Empty>}
      <table className="table">
        <thead><tr><th>#</th><th>Tarefa</th><th>Situação</th><th>Progresso</th><th>Episódio</th><th /></tr></thead>
        <tbody>
          {jobs.data?.map((j) => (
            <tr key={j.id}>
              <td>{j.id}</td>
              <td>{KIND_LABEL[j.kind] ?? j.kind}<br /><span className="muted small">{j.message}</span>
                {j.error && <><br /><span className="error small">{j.error}</span></>}</td>
              <td><Badge status={j.status} /></td>
              <td><div className="meter small-meter"><div style={{ width: `${Math.round(j.progress * 100)}%` }} /></div></td>
              <td>{j.episode_id ? <a href={`#/episodio/${j.episode_id}`}>abrir</a> : "—"}</td>
              <td className="row">
                {(j.status === "queued" || j.status === "running") &&
                  <ActionButton kind="danger" onClick={async () => { await api.cancelJob(j.id); await jobs.reload(); }}>Cancelar</ActionButton>}
                {(j.status === "failed" || j.status === "cancelled") &&
                  <ActionButton onClick={async () => { await api.retryJob(j.id); await jobs.reload(); }}>Repetir</ActionButton>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
