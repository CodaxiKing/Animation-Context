import { ReactNode, useState } from "react";
import { Job } from "./api";

const STATUS_LABEL: Record<string, string> = {
  draft: "rascunho", planning: "planejando", generating: "gerando", review: "em revisão",
  rendering: "renderizando", done: "pronto", failed: "falhou",
  planned: "planejada", ready: "pronta", approved: "aprovada", needs_fix: "corrigir",
  queued: "na fila", running: "rodando", succeeded: "concluída", cancelled: "cancelada",
  ok: "ok", aviso: "aviso", falta: "falta", motor: "motor", partial: "parcial", planned_engine: "planejado",
};
const STATUS_TONE: Record<string, string> = {
  done: "good", approved: "good", succeeded: "good", ok: "good", ready: "info", review: "info",
  failed: "bad", needs_fix: "warn", falta: "bad", aviso: "warn", cancelled: "muted",
  running: "info", generating: "info", planning: "info", rendering: "info", queued: "muted",
};

export function Badge({ status, label }: { status: string; label?: string }) {
  return <span className={`badge tone-${STATUS_TONE[status] ?? "muted"}`}>{label ?? STATUS_LABEL[status] ?? status}</span>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="error" role="alert">{error}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function JobBar({ job, onCancel }: { job: Job | null; onCancel?: () => void }) {
  if (!job) return null;
  const pct = Math.round(job.progress * 100);
  const active = job.status === "queued" || job.status === "running";
  return (
    <div className={`jobbar ${active ? "" : "jobbar-done"}`} aria-live="polite">
      <div className="jobbar-head">
        <strong>{KIND_LABEL[job.kind] ?? job.kind}</strong>
        <Badge status={job.status} />
        <span className="muted">{job.message}</span>
        {active && onCancel && <button className="link" onClick={onCancel}>cancelar</button>}
      </div>
      <div className="meter" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${pct}%` }} />
      </div>
      {job.error && <p className="error">{job.error}</p>}
    </div>
  );
}

export const KIND_LABEL: Record<string, string> = {
  generate_episode: "Planejar cenas", generate_keyframes: "Imagens-chave", generate_voices: "Vozes",
  generate_video_clips: "Clipes de vídeo", export_episode: "Exportar", regenerate_scene: "Refazer cena",
  character_sheet: "Folha de referência", environment_art: "Arte do cenário",
};

/** Botão que mostra "…" enquanto a ação assíncrona roda e exibe o erro, se houver. */
export function ActionButton({ onClick, children, kind = "secondary", disabled }: {
  onClick: () => Promise<unknown>; children: ReactNode; kind?: "primary" | "secondary" | "danger"; disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <span className="action">
      <button className={`btn btn-${kind}`} disabled={busy || disabled} onClick={async () => {
        setBusy(true);
        setErr(null);
        try { await onClick(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
        finally { setBusy(false); }
      }}>{busy ? "…" : children}</button>
      {err && <span className="error small">{err}</span>}
    </span>
  );
}

export function FilePick({ label, accept, onFile }: { label: string; accept: string; onFile: (f: File) => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <label className={`btn btn-secondary file ${busy ? "busy" : ""}`}>
      {busy ? "enviando…" : label}
      <input type="file" accept={accept} hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setBusy(true);
        setErr(null);
        try { await onFile(f); } catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
        finally { setBusy(false); }
      }} />
      {err && <span className="error small">{err}</span>}
    </label>
  );
}

export const seconds = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
