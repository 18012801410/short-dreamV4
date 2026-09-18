const BASE = '/api'

export class ApiError extends Error {
  code: string
  details: Record<string, unknown>
  status: number

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let code = `HTTP_${res.status}`
    let message = `${method} ${path} failed: ${res.status}`
    let details: Record<string, unknown> = {}
    try {
      const payload = (await res.json()) as { error?: { code: string; message: string; details?: Record<string, unknown> } }
      if (payload.error) {
        code = payload.error.code
        message = payload.error.message
        details = payload.error.details ?? {}
      }
    } catch {
      /* 非 JSON 错误体，保留默认 */
    }
    throw new ApiError(res.status, code, message, details)
  }
  return res.json() as Promise<T>
}

export const apiGet = <T,>(path: string) => request<T>('GET', path)
export const apiPost = <T,>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
export const apiPut = <T,>(path: string, body?: unknown) => request<T>('PUT', path, body ?? {})

// ---- 类型（与 API_SPEC 对应） ----

export interface ProjectParams {
  genre: string
  style: string
  dramatic_tone?: 'hook' | 'three_act'
  target_duration_sec: number
  scene_count: number
  ratio: string
  resolution: string
  prompt_lang: 'en' | 'zh'
}

export interface Project {
  id: string
  title: string
  idea: string
  params: ProjectParams
  status: string
  series_id?: string | null
  episode_no?: number | null
  created_at: string
  updated_at: string
  active_script?: ScriptVersion | null
  stage_stats?: { segments_total: number; segments_done: number; assets_total: number }
}

// ---- 系列分剧（TASK-047） ----

export interface SeriesParams {
  genre: string
  style: string
  dramatic_tone?: 'hook' | 'three_act'
  episode_count: number
  per_episode_sec: number
  scene_count: number
  ratio: string
  resolution: string
  prompt_lang: 'en' | 'zh'
}

export interface EpisodeOutline {
  episode_no: number
  title: string
  synopsis: string
  opening_hook: string
  ending_hook: string
  highlight: string
  new_characters: string[]
}

export interface SeriesOutline {
  title: string
  logline: string
  genre_tags: string[]
  characters: { name: string; profile: string }[]
  episodes: EpisodeOutline[]
  climax_episode: number
  warnings: string[]
}

export interface SeriesEpisodeRow {
  episode_no: number
  title: string
  opening_hook: string
  synopsis: string
  highlight: string
  ending_hook: string
  project: Project | null
}

export type SeriesStatus = 'created' | 'outline_drafting' | 'outline_ready' | 'outline_approved'

export interface Series {
  id: string
  title: string
  idea: string
  params: SeriesParams
  status: SeriesStatus
  outline: SeriesOutline | null
  created_at: string
  updated_at: string
  episodes?: SeriesEpisodeRow[]
  episode_projects?: number
  statuses?: string[]
}

export interface DialogueLine {
  speaker: string
  line: string
  tone?: string
}
export type BeatType = 'action' | 'dialogue' | 'sfx' | 'on_screen_text' | 'transition'
export interface Beat {
  type: BeatType
  text: string
  speaker?: string
  tone?: string
}
export interface Scene {
  id: string
  title: string
  summary: string
  dialogues: DialogueLine[]
  est_seconds: number
  beats?: Beat[]
}
export interface ScriptContent {
  logline: string
  scenes: Scene[]
  characters: { name: string; profile: string }[]
  props: string[]
  warnings: string[]
}
export interface ScriptVersion {
  id: string
  version_no: number
  status: 'draft' | 'active' | 'superseded'
  source: string
  content: ScriptContent
}

export interface AssetImage {
  id: string
  asset_id: string
  version_no: number
  view_label: string
  prompt: string
  file_path: string
  url: string | null
  status: 'generating' | 'ready' | 'failed' | 'uploaded'
  approved: boolean
}

export interface FrameImage {
  id: string
  project_id: string
  segment_key: string
  version_no: number
  view_label: string
  prompt: string
  file_path: string
  url: string | null
  /** 本张图实际送入的参考图（TASK-035），页面摊开展示 */
  reference_paths: string[]
  reference_urls: string[]
  status: 'generating' | 'ready' | 'failed' | 'uploaded'
  approved: boolean
}

/** 关键帧所属分镜段的内容上下文（TASK-035） */
export interface FrameSegmentContext {
  scene_id: string
  duration_sec: number
  shot_summaries: string[]
  /** 逐镜认领的剧本节拍（中文）——页面用它展示"这段戏在演什么" */
  shot_beats_zh?: string[][]
  placements: { name: string; placement: string; in_frame: boolean }[]
  auto_reference_paths: string[]
  [key: string]: unknown
}

/** 可作关键帧参考图的候选（已批准且已落盘的资产图） */
export interface FrameReferenceOption {
  image_id: string
  asset_id: string
  kind: 'character' | 'scene' | 'prop'
  name: string
  view_label: string
  path: string
  url: string
}

export interface FramesView {
  frames: FrameImage[]
  keyframe_descriptions: Record<string, string>
  segment_context?: Record<string, FrameSegmentContext>
  reference_options?: FrameReferenceOption[]
}
export interface Asset {
  id: string
  kind: 'character' | 'scene' | 'prop'
  name: string
  description: string
  visual_anchor: string
  image_plan: { view_label: string; image_prompt: string }[]
  images: AssetImage[]
}

export interface Shot {
  shot_no: number
  cutpoint_sec: number
  camera: string
  description: string
  action?: string
  beat_refs?: number[]
  dialogue_refs: DialogueLine[]
}
export interface PictureRef {
  picture_no: number
  kind: 'asset' | 'tail_frame'
  asset_id?: string
  asset_name?: string
  asset_kind?: 'character' | 'scene' | 'prop'
  view_label?: string
  usage_note?: string
  source_segment_key?: string
  label?: string
  url: string | null
  missing?: boolean
  reason?: string
}
export interface SegmentReferences {
  pictures: PictureRef[]
  expected_count: number
  mentioned: number[]
  numbering_ok: boolean
}
export interface Segment {
  segment_key: string
  scene_id: string
  index: number
  duration_sec: number
  shots: Shot[]
  asset_refs: { asset_id: string; usage_note: string }[]
  continuity: { enabled: boolean; with_prev_segment_key: string }
  soundscape?: string
  music?: string
  keyframe_description?: string
  h3_prompt: {
    text: string
    lang: string
    structure_version: string
    /** 人工覆盖（TASK-040）：页面手改过正文，产视频时不再被重编译覆盖 */
    manual_override?: boolean
  }
  resolution_status?: { status: string; assets?: string[] }
  references?: SegmentReferences
  /** 页面所示正文的来源（TASK-040）：compiled = 实时编译（生产同款）/ manual = 人工覆盖 */
  prompt_view?: 'compiled' | 'manual'
  /** 逐镜认领的剧本节拍（中文）：页面按镜展示中文内容 */
  shot_beats_zh?: string[][]
}
export interface StoryboardVersion {
  id: string
  version_no: number
  status: 'draft' | 'active' | 'superseded'
  segments: Segment[]
}

export interface JobError { code: string; message: string; provider_code: string }
export interface JobEvent { at: string; event: string; detail: string }
export interface Job {
  id: string
  type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  phase: string
  attempts: number
  max_attempts: number
  depends_on: string[]
  error: JobError | null
  log: JobEvent[]
  created_at: string
}

export interface ClipInfo {
  id: string
  segment_key: string
  video_job_id: string
  storyboard_version_id: string
  url: string
  tail_frame_url: string | null
  duration_sec: number
  mode: string
}
export interface SegmentRow {
  segment_key: string
  duration_sec: number
  status: 'done' | 'pending'
  clip: ClipInfo | null
  job: Job | null
}

export interface Film {
  id: string
  version_no: number
  url: string | null
  subtitle_url: string | null
  segment_keys: string[]
  duration_sec: number
  status: 'composing' | 'ready' | 'failed'
  error: string
}

export interface SystemHealth {
  status: 'ok' | 'degraded'
  db: 'ok' | 'error'
  ffmpeg: { found: boolean; path: string | null; version: string | null }
  /** 代码新鲜度（TASK-044）：进程启动后改的源码不生效，付费动作会被 STALE_CODE 拒 */
  code?: {
    version: string
    stale: boolean
    process_started_ts: number
    newest_source_ts: number
    newest_source_path: string
  }
  providers_configured: { minimax: boolean; llm: boolean; runninghub: boolean }
}

export interface SettingsView {
  llm_base_url: string
  llm_model: string
  llm_api_key: string
  runninghub_api_key: string
  image_model: string
  video_model: string
  video_concurrency: number
  /** 图片生成并发上限（image_gen + frame_gen 同时在跑数，默认 2） */
  image_concurrency: number
  ffmpeg_path: string
}

// ---- 端点 ----

export const api = {
  health: () => apiGet<SystemHealth>('/system/health'),
  listProjects: (includeArchived = false) =>
    apiGet<{ projects: Project[] }>(`/projects?include_archived=${includeArchived}`),
  listSeries: () => apiGet<{ series: Series[] }>('/series'),
  getSeries: (sid: string) => apiGet<{ series: Series }>(`/series/${sid}`),
  createSeries: (title: string, idea: string, params: Partial<SeriesParams>) =>
    apiPost<{ series: Series }>('/series', { title, idea, params }),
  seriesCommand: (sid: string, type: string, payload?: Record<string, unknown>) =>
    apiPost<{
      ok?: boolean
      jobs?: Job[]
      series?: Series
      project?: Project
      copied_assets?: number
      copied_images?: number
    }>(`/series/${sid}/commands`, { type, payload: payload ?? {} }),
  getProject: (pid: string) => apiGet<Project>(`/projects/${pid}`),
  createProject: (title: string, idea: string, params: Partial<ProjectParams>) =>
    apiPost<{ project: Project }>('/projects', { title, idea, params }),
  command: (pid: string, type: string, payload?: Record<string, unknown>) =>
    apiPost<{ ok?: boolean; jobs?: Job[]; project?: Project; stopped?: boolean }>(
      `/projects/${pid}/commands`,
      { type, payload: payload ?? {} },
    ),
  translatePrompt: (
    pid: string,
    text: string,
    mode: 't2i' | 'edit' = 't2i',
    currentPrompt = '',
  ) =>
    apiPost<{ prompt: string }>(`/projects/${pid}/translate_prompt`, {
      text,
      mode,
      current_prompt: currentPrompt,
    }),
  script: (pid: string) =>
    apiGet<{ active: ScriptVersion | null; draft: ScriptVersion | null; versions: ScriptVersion[] }>(
      `/projects/${pid}/script`,
    ),
  assets: (pid: string) => apiGet<{ assets: Asset[] }>(`/projects/${pid}/assets`),
  frames: (pid: string) => apiGet<FramesView>(`/projects/${pid}/frames`),
  storyboard: (pid: string) =>
    apiGet<{ active: StoryboardVersion | null; draft: StoryboardVersion | null }>(
      `/projects/${pid}/storyboard`,
    ),
  jobs: (pid: string) => apiGet<{ jobs: Job[] }>(`/projects/${pid}/jobs`),
  retryJob: (jobId: string) => apiPost<{ jobs: Job[] }>(`/jobs/${jobId}/retry`),
  cancelJob: (jobId: string) => apiPost<{ jobs: Job[] }>(`/jobs/${jobId}/cancel`),
  segments: (pid: string) => apiGet<{ segments: SegmentRow[] }>(`/projects/${pid}/segments`),
  films: (pid: string) => apiGet<{ films: Film[] }>(`/projects/${pid}/film`),
  settings: () => apiGet<SettingsView>('/settings'),
  saveSettings: (body: Partial<SettingsView>) => apiPut<SettingsView>('/settings', body),
  testSettings: () =>
    apiPost<{ llm: { ok: boolean; error?: string }; image: { ok: boolean }; video: { ok: boolean } }>(
      '/settings/test',
    ),
  uploadAssetImage: (pid: string, assetId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/projects/${pid}/assets/${assetId}/upload_image`, {
      method: 'POST',
      body: form,
    }).then(async (res): Promise<{ ok: boolean; asset_image: AssetImage }> => {
      if (!res.ok) throw new ApiError(res.status, 'UPLOAD_FAILED', `上传失败：${res.status}`)
      return (await res.json()) as { ok: boolean; asset_image: AssetImage }
    })
  },
  uploadFrameImage: (pid: string, segmentKey: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`${BASE}/projects/${pid}/segments/${segmentKey}/upload_frame`, {
      method: 'POST',
      body: form,
    }).then(async (res): Promise<{ ok: boolean; frame_image: FrameImage }> => {
      if (!res.ok) throw new ApiError(res.status, 'UPLOAD_FAILED', `上传失败：${res.status}`)
      return (await res.json()) as { ok: boolean; frame_image: FrameImage }
    })
  },
}
