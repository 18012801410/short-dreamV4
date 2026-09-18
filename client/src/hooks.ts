import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Project } from './api/client'

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 8000 })
}

export function useSeriesList() {
  // 轮询：大纲生成在 Worker 内完成，状态需要自动刷新
  return useQuery({
    queryKey: ['series'],
    queryFn: api.listSeries,
    refetchInterval: 4000,
  })
}

export function useSeries(sid: string) {
  return useQuery({
    queryKey: ['series', sid],
    queryFn: () => api.getSeries(sid),
    refetchInterval: 3000,
  })
}

export function useCreateSeries() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      title,
      idea,
      params,
    }: {
      title: string
      idea: string
      params?: Record<string, unknown>
    }) => api.createSeries(title, idea, params ?? {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['series'] }),
  })
}

export function useSeriesCommand(sid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ type, payload }: { type: string; payload?: Record<string, unknown> }) =>
      api.seriesCommand(sid, type, payload),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['series', sid] })
      void qc.invalidateQueries({ queryKey: ['series'] })
      void qc.invalidateQueries({ queryKey: ['projects'] })
    },
  })
}

export function useProjects(includeArchived = false) {
  // 轮询：Worker 在后台完成任务后，列表的「继续」按钮与状态自动更新
  return useQuery({
    queryKey: ['projects', includeArchived],
    queryFn: () => api.listProjects(includeArchived),
    refetchInterval: 5000,
  })
}

export function useCreateProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      title,
      idea,
      params,
    }: {
      title: string
      idea: string
      params?: {
        dramatic_tone?: 'hook' | 'three_act'
        target_duration_sec?: number
        scene_count?: number
      }
    }) => api.createProject(title, idea, params ?? {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['projects'] }),
  })
}

/** 删除项目：硬删除（DB 级联清除 + 媒体移入 data/trash），成功后刷新项目列表 */
export function useDeleteProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (pid: string) => api.deleteProject(pid),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['series'] })
    },
  })
}

export function useProject(pid: string) {
  return useQuery({
    queryKey: ['project', pid],
    queryFn: () => api.getProject(pid),
    refetchInterval: 3000,
  })
}

export function useScript(pid: string) {
  // 轮询：script_gen 任务在 Worker 内完成，草稿需要自动出现
  return useQuery({
    queryKey: ['script', pid],
    queryFn: () => api.script(pid),
    refetchInterval: 3000,
  })
}

export function useAssets(pid: string) {
  return useQuery({
    queryKey: ['assets', pid],
    queryFn: () => api.assets(pid),
    refetchInterval: 3000,
  })
}

export function useFrames(pid: string) {
  // 轮询：frame_gen 任务在 Worker 内完成，关键帧图需要自动出现
  return useQuery({
    queryKey: ['frames', pid],
    queryFn: () => api.frames(pid),
    refetchInterval: 3000,
  })
}

export function useStoryboard(pid: string) {
  // 轮询：storyboard_gen 任务在 Worker 内完成，分镜草稿需要自动出现
  return useQuery({
    queryKey: ['storyboard', pid],
    queryFn: () => api.storyboard(pid),
    refetchInterval: 3000,
  })
}

export function useJobs(pid: string) {
  return useQuery({
    queryKey: ['jobs', pid],
    queryFn: () => api.jobs(pid),
    refetchInterval: 2000,
  })
}

export function useSegments(pid: string) {
  return useQuery({
    queryKey: ['segments', pid],
    queryFn: () => api.segments(pid),
    refetchInterval: 2500,
  })
}

export function useFilms(pid: string) {
  return useQuery({
    queryKey: ['films', pid],
    queryFn: () => api.films(pid),
    refetchInterval: 3000,
  })
}

/** 命令 mutation：成功后使项目相关查询失效（轮询自然刷新）。 */
export function useCommand(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ type, payload }: { type: string; payload?: Record<string, unknown> }) =>
      api.command(pid, type, payload),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['project', pid] })
      void qc.invalidateQueries({ queryKey: ['jobs', pid] })
      void qc.invalidateQueries({ queryKey: ['segments', pid] })
      void qc.invalidateQueries({ queryKey: ['script', pid] })
      void qc.invalidateQueries({ queryKey: ['assets', pid] })
      void qc.invalidateQueries({ queryKey: ['frames', pid] })
      void qc.invalidateQueries({ queryKey: ['storyboard', pid] })
      void qc.invalidateQueries({ queryKey: ['films', pid] })
    },
  })
}

export function useRetryJob(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['jobs', pid] }),
  })
}

export function useCancelJob(pid: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['jobs', pid] }),
  })
}

export function projectStatusLabel(status: string): string {
  const map: Record<string, string> = {
    CREATED: '已创建',
    SCRIPT_DRAFTING: '剧本生成中',
    SCRIPT_READY: '剧本待确认',
    SCRIPT_APPROVED: '剧本已确认',
    ASSET_DRAFTING: '资产生成中',
    ASSET_READY: '资产待确认',
    ASSET_APPROVED: '资产已确认',
    STORYBOARD_DRAFTING: '分镜生成中',
    STORYBOARD_READY: '分镜待确认',
    STORYBOARD_APPROVED: '分镜已确认',
    FRAME_DRAFTING: '关键帧生成中',
    FRAME_READY: '关键帧待确认',
    FRAME_APPROVED: '关键帧已确认',
    VIDEO_PRODUCING: '视频生产中',
    VIDEO_READY: '视频就绪',
    COMPOSING: '合成中',
    COMPOSED: '已完成',
    ARCHIVED: '已归档',
  }
  return map[status] ?? status
}

export function isProjectBusy(status: Project['status']): boolean {
  return status.endsWith('_DRAFTING') || status === 'VIDEO_PRODUCING' || status === 'COMPOSING'
}
