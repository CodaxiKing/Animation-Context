import { useCallback, useEffect, useRef, useState } from "react";
import { api, Job } from "./api";

/** Carrega dados e expõe recarga; erros viram mensagem legível. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadRef = useRef(load);
  loadRef.current = load;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loadRef.current());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void reload(); }, deps);
  return { data, error, loading, reload, setData };
}

const FINISHED = new Set(["succeeded", "failed", "cancelled"]);

/** Acompanha uma tarefa da fila até terminar (consulta a cada segundo). */
export function useJob(onFinish?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const timer = useRef<number | null>(null);
  const finishRef = useRef(onFinish);
  finishRef.current = onFinish;

  const stop = () => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
  };
  const track = useCallback((jobId: number) => {
    stop();
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        setJob(j);
        if (FINISHED.has(j.status)) {
          stop();
          finishRef.current?.(j);
        }
      } catch { /* API reiniciando: tenta de novo no próximo tique */ }
    };
    void tick();
    timer.current = window.setInterval(tick, 1000);
  }, []);
  useEffect(() => stop, []);
  const running = job !== null && !FINISHED.has(job.status);
  return { job, track, running, clear: () => setJob(null) };
}

export function useHashRoute(): string[] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [parts, setParts] = useState(parse);
  useEffect(() => {
    const on = () => setParts(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}
