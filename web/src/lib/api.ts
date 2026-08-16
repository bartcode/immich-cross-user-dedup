export interface CheckDto {
  name: string
  ok: boolean
  detail: string
}

export interface ConfigDto {
  immich_url: string
  primary_email: string
  secondary_email: string
  partners_bidirectional: boolean
  checks: CheckDto[]
}

export interface JobDto {
  kind: string | null
  running: boolean
  stage: string
  current: number
  total: number | null
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export interface StatsDto {
  primary_email: string
  secondary_email: string
  primary_assets: number
  secondary_assets: number
  pair_count: number
  excluded_count: number
  eligible_count: number
  reclaimable_assets: number
  reclaimable_bytes: number
  affected_albums: number
  live_photo_aligned: number
  live_photo_keeper_lacks_motion: number
  live_photo_loser_lacks_motion: number
}

export interface AssetDto {
  id: string
  owner_role: string
  type: string
  file_name: string
  taken_at: string | null
  size_bytes: number
  is_favorite: boolean
  description: string
  is_live_photo: boolean
  url: string
  thumbnail_url: string
  albums?: { id: string; name: string; owner_role: string }[]
}

export interface PairDto {
  checksum: string
  excluded: boolean
  live_photo: string
  keeper: AssetDto
  loser: AssetDto
  reclaimable_bytes: number
}

export interface PairsResponse {
  total: number
  items: PairDto[]
}

export interface JournalSummaryDto {
  name: string
  size_bytes: number
  modified: string
}

export interface JournalDetailDto {
  name: string
  entries: Record<string, unknown>[]
  undo_preview: {
    trash_entries: number
    trashed_assets: number
    album_adds: number
    metadata_merges: number
  }
}

export interface UndoResultDto {
  kind: string
  restored_assets?: number
  unrestorable?: string[]
  album_rows_removed?: number
  album_rows_kept?: number
  metadata_restored?: number
  errors?: string[]
}

export interface JobStatusResponse {
  job: JobDto
  stats: StatsDto | null
  last_result: Record<string, unknown> | null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  })
  if (!response.ok) {
    let detail = `${response.status}`
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  config: () => request<ConfigDto>("/api/config"),
  scan: () => request<JobDto>("/api/scan", { method: "POST" }),
  job: () => request<JobStatusResponse>("/api/job"),
  stats: () => request<StatsDto>("/api/stats"),
  pairs: (filter: string, offset: number, limit: number) =>
    request<PairsResponse>(`/api/pairs?filter=${filter}&offset=${offset}&limit=${limit}`),
  pair: (checksum: string) => request<PairDto>(`/api/pairs/${checksum}`),
  exclude: (checksum: string) => request<unknown>(`/api/pairs/${checksum}/exclude`, { method: "POST" }),
  include: (checksum: string) => request<unknown>(`/api/pairs/${checksum}/include`, { method: "POST" }),
  apply: (body: { merge_metadata: boolean; live_photo_motion: string; limit: number | null }) =>
    request<JobDto>("/api/apply", { method: "POST", body: JSON.stringify(body) }),
  journals: () => request<JournalSummaryDto[]>("/api/journals"),
  journal: (name: string) => request<JournalDetailDto>(`/api/journals/${encodeURIComponent(name)}`),
  undo: (name: string) => request<JobDto>("/api/undo", { method: "POST", body: JSON.stringify({ name }) }),
}

export function humanBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"]
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return unit === 0 ? `${value} B` : `${value.toFixed(1)} ${units[unit]}`
}
