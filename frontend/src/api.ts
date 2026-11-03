// Cliente tipado da API do estúdio (backend/lofi_studio/api.py).

export type Series = {
  id: number; slug: string; title: string; genre: string; style_preset: string;
  synopsis: string; motor_slug: string | null; created_at: string;
};
export type Character = {
  id: number; series_id: number; slug: string; name: string; description: string;
  rig: Record<string, unknown>; locked: boolean; reference_assets: number[];
  voice: { sample_asset?: number; exaggeration?: number; cfg_weight?: number; temperature?: number; seed?: number };
  pronoun?: string | null;
};
export type Environment = {
  id: number; series_id: number; slug: string; name: string; description: string;
  motor_scene: string | null; reference_assets: number[];
};
export type EpisodeStatus = "draft" | "planning" | "generating" | "review" | "rendering" | "done" | "failed";
export type Finding = { severity: string; code: string; message: string };
export type Episode = {
  id: number; series_id: number; number: number; title: string; script: string; status: EpisodeStatus;
  spec: { total?: number; voices?: Record<string, string>; clips?: unknown[] };
  quality: { ok?: boolean; findings?: Finding[] }; output_path: string | null;
};
export type SceneStatus = "planned" | "generating" | "ready" | "approved" | "needs_fix" | "failed";
export type Actor = { char: string; emotion?: string; view?: string; action_pose?: string };
export type ActionStep = { key: string; motion: string; moving: boolean };
export type Scene = {
  id: number; episode_id: number; index: number; start: number; end: number; text: string;
  environment: string | null; status: SceneStatus; review_notes: string; version: number;
  preview_path: string | null;
  shot: {
    actors?: Actor[]; video_clip?: string; video_parts?: number;
    director_notes?: { speaker?: string | null; dialogue?: string | null; cast?: string[];
                       actions?: ActionStep[]; action_scene?: boolean; shot?: string };
  };
};
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type Job = {
  id: number; kind: string; status: JobStatus; progress: number; message: string;
  params: Record<string, unknown>; result: Record<string, unknown>; error: string | null;
  episode_id: number | null; scene_id: number | null; created_at: string; finished_at: string | null;
};
export type Engine = { id: string; name: string; status: string; provider: string; notes: string };
export type PreflightItem = { item: string; status: "ok" | "aviso" | "falta"; detail: string; fix: string };
export type Preflight = { ready: boolean; items: PreflightItem[] };
export type ComfyInstance = {
  name: string; url: string; online: boolean; last_error: string | null; queue_remaining: number;
  devices: { name: string; vram_total_mb: number; vram_free_mb: number }[];
};
export type VoicePlan = {
  lines: { scene: number; voice: string; text: string; emotion: string; ready: boolean }[];
  missing_samples: string[];
};
export type KeyframePlan = { missing: string[]; images: Record<string, { kind: string; exists: boolean }> };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch { /* corpo sem JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const get = <T>(p: string) => request<T>("GET", p);
const post = <T>(p: string, b: unknown = {}) => request<T>("POST", p, b);
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const upload = <T>(p: string, file: File) => {
  const fd = new FormData();
  fd.append("file", file);
  return request<T>("POST", p, fd);
};
type JobRef = { job_id: number };

export const api = {
  health: () => get<{ ok: boolean; version: string; motor: true | string; motor_scenes: number; ffmpeg: boolean }>("/health"),
  engines: () => get<Engine[]>("/api/engines"),
  styles: () => get<{ id: string; name: string; description: string }[]>("/api/styles"),
  preflight: (comfy = true, voice = true) => get<Preflight>(`/api/preflight?comfy=${comfy}&voice=${voice}`),

  series: () => get<Series[]>("/api/series"),
  createSeries: (b: Partial<Series>) => post<Series>("/api/series", b),
  motorSeries: () => get<string[]>("/api/motor/series"),
  importSeries: (slug: string) => post<Series>(`/api/motor/series/${slug}/import`),
  motorScenes: () => get<string[]>("/api/motor/scenes"),

  characters: (sid: number) => get<Character[]>(`/api/series/${sid}/characters`),
  createCharacter: (sid: number, b: Partial<Character>) => post<Character>(`/api/series/${sid}/characters`, b),
  updateCharacter: (id: number, b: Partial<Character> & { unlock?: boolean }) => patch<Character>(`/api/characters/${id}`, b),
  uploadReference: (id: number, f: File) => upload<Character>(`/api/characters/${id}/reference`, f),
  uploadVoice: (id: number, f: File) => upload<Character>(`/api/characters/${id}/voice-sample`, f),
  setVoice: (id: number, b: Character["voice"]) => put<Character>(`/api/characters/${id}/voice`, b),
  generateSheet: (id: number) => post<JobRef>(`/api/characters/${id}/generate-sheet`),

  environments: (sid: number) => get<Environment[]>(`/api/series/${sid}/environments`),
  createEnvironment: (sid: number, b: Partial<Environment>) => post<Environment>(`/api/series/${sid}/environments`, b),
  generateEnvironmentArt: (id: number) => post<JobRef>(`/api/environments/${id}/generate-art`),

  episodes: (sid: number) => get<Episode[]>(`/api/series/${sid}/episodes`),
  episode: (id: number) => get<Episode>(`/api/episodes/${id}`),
  createEpisode: (sid: number, b: { number: number; title: string; script: string }) =>
    post<Episode>(`/api/series/${sid}/episodes`, b),
  updateEpisode: (id: number, b: { title?: string; script?: string }) => patch<Episode>(`/api/episodes/${id}`, b),
  scenes: (eid: number) => get<Scene[]>(`/api/episodes/${eid}/scenes`),
  keyframePlan: (eid: number) => get<KeyframePlan>(`/api/episodes/${eid}/keyframes`),
  voicePlan: (eid: number) => get<VoicePlan>(`/api/episodes/${eid}/voices`),

  generate: (eid: number) => post<JobRef>(`/api/episodes/${eid}/generate`),
  keyframes: (eid: number, b: Record<string, unknown> = {}) => post<JobRef>(`/api/episodes/${eid}/keyframes`, b),
  voices: (eid: number, b: Record<string, unknown> = {}) => post<JobRef>(`/api/episodes/${eid}/voices`, b),
  clips: (eid: number, b: Record<string, unknown>) => post<JobRef>(`/api/episodes/${eid}/video-clips`, b),
  exportEpisode: (eid: number, allowUnapproved: boolean) =>
    post<JobRef>(`/api/episodes/${eid}/export`, { allow_unapproved: allowUnapproved }),

  reviewScene: (id: number, b: { status?: SceneStatus; review_notes?: string }) => patch<Scene>(`/api/scenes/${id}`, b),
  regenerateScene: (id: number, shotPatch: Record<string, unknown>) =>
    post<JobRef>(`/api/scenes/${id}/regenerate`, { shot_patch: shotPatch }),

  jobs: (episodeId?: number) => get<Job[]>(`/api/jobs${episodeId ? `?episode_id=${episodeId}` : ""}`),
  job: (id: number) => get<Job>(`/api/jobs/${id}`),
  cancelJob: (id: number) => post<Job>(`/api/jobs/${id}/cancel`),
  retryJob: (id: number) => post<JobRef>(`/api/jobs/${id}/retry`),

  comfyInstances: () => get<ComfyInstance[]>("/api/comfyui/instances"),
  comfyDiscover: () => post<ComfyInstance[]>("/api/comfyui/discover"),
  comfyAdd: (name: string, url: string) => post<ComfyInstance>("/api/comfyui/instances", { name, url }),
};

export const assetUrl = (id: number) => `/api/assets/${id}/file`;
export const previewUrl = (s: Scene) => `/api/scenes/${s.id}/preview?v=${s.version}`;
export const videoUrl = (e: Episode) => `/api/episodes/${e.id}/video?t=${encodeURIComponent(e.output_path ?? "")}`;
