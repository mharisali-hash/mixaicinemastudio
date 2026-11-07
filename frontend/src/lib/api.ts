import type {
  AssistAction,
  AudioAnalysis,
  AudioEdit,
  AudioExportFormat,
  AudioGenerateRequest,
  AudioPreview,
  AerabResponse,
  TextAerabRequest,
  TextAerabResponse,
  LoudnessTarget,
  MixIn,
  Peaks,
  LyricsRequest,
  LyricsResponse,
  TemplateType,
  BoardLayout,
  Character,
  CharacterProfile,
  CharacterSheet,
  CharacterVoiceDesign,
  TestClip,
  SheetCatalogue,
  SheetCrop,
  SheetRequest,
  SheetTextPatch,
  PhotoConsentRecord,
  ShotRef,
  Generation,
  GenerationKind,
  Job,
  JobStatus,
  Location,
  Project,
  ProjectCreate,
  QuickRecent,
  QuickRequest,
  RegenerateMode,
  Reel,
  ReelClip,
  ReelClipPatch,
  ReelEstimate,
  Render,
  Scene,
  ScenePatch,
  Suggestion,
  Shot,
  ShotEstimate,
  ShotExtendResult,
  ShotRerenderResult,
  ShotsApproveResult,
  ShotPatch,
  ShotType,
  StitchRequest,
  StoryboardRequest,
  SuggestionResult,
  SystemStatus,
  TargetType,
  ImageUpscaleOptions,
  ImageUpscaleRequest,
  UpscaleOptions,
  UpscaleRequest,
  User,
  AdminUser,
  AdminUserDetail,
  AdminUserPatch,
  AdminUserStatus,
  AdminOverview,
  AuditRow,
  Package,
  PackageInput,
  Payment,
  PaymentDetail,
  PaymentInput,
  PaymentStatus,
  QuotaRefusal,
  Subscription,
  SubscriptionFilter,
  Usage,
  UsageGrant,
  UsageReport,
  ApiToken,
  AuthConfig,
  ApiTokenCreate,
  ApiTokenCreated,
  ComfyCheck,
  Dashboard,
  ImageEditRequest,
  BrandImageRequest,
  ImageGenerateRequest,
  ImageToolInfo,
  ImageControlRequest,
  ImageOptions,
  ControlStatus,
  UserTemplateRequest,
  LlmCheck,
  MediaBatch,
  MediaDetail,
  MediaItem,
  MediaPage,
  MediaQuery,
  BatchAction,
  BatchResult,
  Folder,
  FolderKind,
  SavedFilter,
  Template,
  TemplateStart,
  ModelInfo,
  ModelType,
  TakesRequest,
  VideoGenerateRequest,
  Img2ImgRequest,
  EstimateQuery,
  EstimateResult,
  Make3DRequest,
  Make3DResponse,
  Turntable3DResult,
  Shots3DResult,
  PromptEnhanceRequest,
  PromptEnhanceResult,
  MediaKind,
  UpscalePage,
  DesignPreviewRequest,
  DesignPreviewResponse,
  SpeakRequest,
  SpeakResponse,
  Voice,
  VoiceCreate,
  VoiceEngine,
  VoicePatch,
  VoiceQuery,
  VideoPodcast,
  VideoPodcastPatch,
  VPCharacterOption,
  VPEstimate,
  VPQuality,
  VPRegenerate,
} from './types'
import type { BrandKit, BrandKitPatch, LogoRevealRequest, PreviewKind } from './brand'
import type { CameraPreset } from './camera'
import type { MentionOption } from './mentions'
import type { Album, Client, Facets, PhotoPage } from './catalogue'
import type { RenderResponse, Script, ScriptCreate, ScriptPatch, ScriptSummary, SplitMode } from './dialogue'
import type { Dub, DubCreate, DubExportBody, DubPatch, DubSummary, Transcript, TranscriptSummary } from './dub'
import type { GalleryQuery, GalleryResponse, GallerySample } from './gallery'
import type { StemsMode, StemsResponse } from './stems'
import type { Ad, EditOp, Episode, EpisodeCreate, EpisodePatch, EpisodeSummary, InsertBody, PublishBody, Show, ShowCreate, ShowPatch, VideoBody } from './podcast'
import type {
  AutoResult, Background, Described, DevelopParams, EffectSpec, Edge, ExportFormat, Histogram, ImportResult, Look, PhotoHistory, PhotoSchema, ProfileInfo,
  PlanStep, PreviewResult, RestoreRequest, RestoreTool, SmartPlan, Snapshot, UprightMode, UprightResult,
} from './photo/types'

type CameraPresets = Record<'shot_size' | 'angle' | 'motion' | 'speed', (CameraPreset<string> & { phrase: string })[]> & { default_speed: string }

export const API_BASE = '/api'

const UNREACHABLE = "Can't reach the studio server. Check that the backend is running."

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401

let unauthorizedHandler: (() => void) | null = null

// Wired once in main.tsx so a 401 anywhere drops the cached user and the auth guard redirects.
export function onUnauthorized(handler: (() => void) | null) {
  unauthorizedHandler = handler
}

let quotaHandler: ((refusal: QuotaRefusal) => void) | null = null

// contract v39: a 429 with the package numbers in it; wired once in main.tsx to show a calm toast
export function onQuota(handler: ((refusal: QuotaRefusal) => void) | null) {
  quotaHandler = handler
}

export const isQuotaRefusal = (body: unknown): body is QuotaRefusal =>
  !!body && typeof body === 'object' && 'limit_min' in body && 'reason' in body

// Login failing with 401 is a wrong password, not an expired session.
const NO_REDIRECT_ON_401 = ['/auth/login']

function messageFrom(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  // FastAPI 422: detail is [{loc, msg, type}]
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const field = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== 'body').join('.') : ''
        return field ? `${field}: ${d.msg}` : String(d?.msg ?? '')
      })
      .join('; ')
  }
  // the dev proxy answers an empty 500/502 when the backend is down
  if (status >= 500 && (body === null || body === '')) return UNREACHABLE
  if (status >= 500) return 'The server hit a problem. Try again in a moment.'
  return `Request failed (${status})`
}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query) {
  if (!query) return path
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `${path}?${s}` : path
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_BASE + withQuery(path, query), {
      method,
      credentials: 'include',
      headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, UNREACHABLE)
  }

  if (res.status === 204) return undefined as T

  let parsed: unknown = null
  const text = await res.text()
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = text
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !NO_REDIRECT_ON_401.includes(path)) unauthorizedHandler?.()
    if (res.status === 429 && isQuotaRefusal(parsed)) quotaHandler?.(parsed)
    throw new ApiError(res.status, messageFrom(res.status, parsed), parsed)
  }
  return parsed as T
}

// a bare list, or one wrapped as {items}: both shapes are in use across the API
const listOf = <T>(res: T[] | { items: T[] } | null | undefined): T[] => (Array.isArray(res) ? res : (res?.items ?? []))

const get = <T>(path: string, query?: Query) => request<T>('GET', path, undefined, query)
const post = <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {})
const patch = <T>(path: string, body: unknown) => request<T>('PATCH', path, body)
const del = (path: string) => request<void>('DELETE', path)

export async function postBlob(path: string, body: unknown): Promise<Blob> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'image/png' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, UNREACHABLE)
  }
  if (!res.ok) {
    const text = await res.text()
    let parsed: unknown = text
    try {
      parsed = text ? JSON.parse(text) : null
    } catch {
      /* plain text */
    }
    throw new ApiError(res.status, messageFrom(res.status, parsed), parsed)
  }
  return res.blob()
}

// raw fetch for the binary and multipart photo routes; same error shape as request()
export async function send(path: string, init: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch(API_BASE + path, { credentials: 'include', ...init })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError(0, UNREACHABLE)
  }
  if (!res.ok) {
    const text = await res.text()
    let parsed: unknown = text
    try {
      parsed = text ? JSON.parse(text) : null
    } catch {
      /* plain text */
    }
    if (res.status === 401) unauthorizedHandler?.()
    throw new ApiError(res.status, messageFrom(res.status, parsed), parsed)
  }
  return res
}

const jsonInit = (body: unknown, signal?: AbortSignal): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
  signal,
})

const header = (res: Response, name: string) => {
  const raw = res.headers.get(name)
  const n = raw == null || raw === '' ? NaN : Number(raw)
  return Number.isFinite(n) ? n : null
}

// the audio previews answer mp3 with what they measured in headers
async function audioPreview(res: Response): Promise<AudioPreview> {
  return {
    url: URL.createObjectURL(await res.blob()),
    durationS: header(res, 'X-Duration-S'),
    lufsI: header(res, 'X-Lufs-I'),
    truePeakDb: header(res, 'X-True-Peak-Db'),
    renderMs: header(res, 'X-Render-Ms'),
  }
}

export interface GenerationQuery {
  target_type: TargetType
  target_id: string
  kind?: GenerationKind
  include_rejected?: boolean
}

export interface PhotoExif {
  captured_at: string | null
  camera: string | null
  lens: string | null
  focal_mm: number | null
  aperture: number | null
  shutter: string | null
  iso: number | null
  gps: { lat: number; lng: number } | null
  original_name: string | null
  bytes: number | null
  source_type: string | null
  width: number | null
  height: number | null
  tags: Record<string, string>
}

// the public site's price list: only packages the admin marked "Show on the website", already sorted
export interface SitePlan {
  id: string
  name: string
  description: string
  price_pkr: number
  period: 'day' | 'week' | 'month' | 'year' | 'none'
  days: number | null
  private_items: boolean
  studios: string[]
}

export const api = {
  site: {
    plans: () => get<SitePlan[]>('/site/plans'),
  },
  auth: {
    login: (email: string, password: string) => post<User>('/auth/login', { email, password }),
    logout: () => post<void>('/auth/logout'),
    me: () => get<User>('/auth/me'),
    // contract v38
    config: () => get<AuthConfig>('/auth/config'),
    signup: (body: { email: string; password: string; name: string }) => post<{ detail: string }>('/auth/signup', body),
    resendVerification: (email?: string) => post<{ detail: string }>('/auth/verify/resend', email ? { email } : {}),
    forgot: (email: string) => post<{ detail: string }>('/auth/forgot', { email }),
    reset: (token: string, password: string) => post<User>('/auth/reset', { token, password }),
    changePassword: (current_password: string, new_password: string) =>
      post<User>('/auth/password', { current_password, new_password }),
    logoutEverywhere: () => post<void>('/auth/logout-everywhere'),
    updateMe: (display_name: string) => patch<User>('/auth/me', { display_name }),
    // dev-only prefill; the server answers {enabled:false} anywhere but a local dev run
    devLogin: () => get<{ enabled: boolean; email?: string; password?: string }>('/auth/dev-login'),
  },
  // contract v38: admin only; everyone else gets 404
  admin: {
    users: (q = '', status: AdminUserStatus = 'all', packageId = '') =>
      get<AdminUser[]>('/admin/users', { q, status, package_id: packageId }),
    createUser: (body: { email: string; name: string; admin: boolean }) => post<AdminUser>('/admin/users', body),
    patchUser: (id: string, body: AdminUserPatch) => patch<AdminUser>(`/admin/users/${id}`, body),
    sendReset: (id: string) => post<{ detail: string }>(`/admin/users/${id}/send-reset`),
    resendVerification: (id: string) => post<{ detail: string }>(`/admin/users/${id}/resend-verification`),
    // contract v39: the admin panel
    user: (id: string) => get<AdminUserDetail>(`/admin/users/${id}`),
    assignPackage: (id: string, body: { package_id: string; starts_at?: string | null; ends_at?: string | null; notes?: string }) =>
      post<Subscription>(`/admin/users/${id}/subscription`, body),
    startDemo: (id: string, body: { package_id?: string | null; allow_repeat?: boolean } = {}) => post<Subscription>(`/admin/users/${id}/demo`, body),
    grantMinutes: (id: string, body: { gpu_min: number; reason?: string; expires_at?: string | null }) =>
      post<UsageGrant>(`/admin/users/${id}/grants`, body),
    revokeGrant: (grantId: string) => post<UsageGrant>(`/admin/grants/${grantId}/revoke`),
    customPackage: (id: string, body: Partial<PackageInput> & { name: string; ends_at?: string | null }) =>
      post<Subscription>(`/admin/users/${id}/custom-package`, body),
    overview: () => get<AdminOverview>('/admin/overview'),
    packages: (archived = false) => get<Package[]>('/admin/packages', { archived }),
    createPackage: (body: PackageInput) => post<Package>('/admin/packages', body),
    patchPackage: (id: string, body: Partial<PackageInput> & { active?: boolean }) => patch<Package>(`/admin/packages/${id}`, body),
    duplicatePackage: (id: string) => post<Package>(`/admin/packages/${id}/duplicate`),
    archivePackage: (id: string) => post<Package>(`/admin/packages/${id}/archive`),
    restorePackage: (id: string) => post<Package>(`/admin/packages/${id}/restore`),
    subscriptions: (status: SubscriptionFilter = 'active', packageId = '') =>
      get<Subscription[]>('/admin/subscriptions', { status, package_id: packageId }),
    extendSubscription: (id: string, body: { days?: number; ends_at?: string }) => post<Subscription>(`/admin/subscriptions/${id}/extend`, body),
    cancelSubscription: (id: string) => post<Subscription>(`/admin/subscriptions/${id}/cancel`),
    payments: (status: PaymentStatus | 'all' = 'all', gateway = '') => get<Payment[]>('/admin/payments', { status, gateway }),
    payment: (id: string) => get<PaymentDetail>(`/admin/payments/${id}`),
    recordPayment: (body: PaymentInput) => post<PaymentDetail>('/admin/payments', body),
    markPayment: (id: string, status: 'paid' | 'refunded' | 'failed', notes = '') =>
      post<PaymentDetail>(`/admin/payments/${id}/mark`, { status, notes }),
    usage: (days = 30, userId = '') => get<UsageReport>('/admin/usage', { days, user_id: userId }),
    usageCsvUrl: (days = 30, userId = '') => `${API_BASE}/admin/usage.csv?days=${days}${userId ? `&user_id=${encodeURIComponent(userId)}` : ''}`,
    audit: (action = '', q = '') => get<AuditRow[]>('/admin/audit', { action, q }),
  },
  // contract v39: your own package and GPU meter
  account: {
    usage: () => get<Usage>('/account/usage'),
  },
  // contract v27: browser session only; the server refuses these with a token
  tokens: {
    list: () => get<ApiToken[]>('/tokens'),
    create: (body: ApiTokenCreate) => post<ApiTokenCreated>('/tokens', body),
    revoke: (id: string) => del(`/tokens/${id}`),
  },
  system: {
    status: () => get<SystemStatus>('/system/status'),
    upscaleOptions: () => get<UpscaleOptions>('/system/upscale-options'),
    comfyCheck: () => get<ComfyCheck>('/system/comfy-check'),
    // ping=true loads each role model in turn, so only on request
    llmCheck: (ping = false) => get<LlmCheck>('/system/llm-check', { ping }),
    imageUpscaleOptions: (generationId: string) =>
      get<ImageUpscaleOptions>(`/system/upscale-options?generation_id=${encodeURIComponent(generationId)}`),
  },
  projects: {
    list: () => get<Project[]>('/projects'),
    get: (id: string) => get<Project>(`/projects/${id}`),
    create: (body: ProjectCreate) => post<Project>('/projects', body),
    update: (id: string, body: Partial<Project>) => patch<Project>(`/projects/${id}`, body),
    remove: (id: string) => del(`/projects/${id}`),
    setBrandKit: (id: string, kitId: string | null) =>
      request<{ project_id: string; brand_kit_id: string | null }>('PUT', `/projects/${id}/brand-kit`, { kit_id: kitId }),
  },
  scenes: {
    list: (projectId: string) => get<Scene[]>(`/projects/${projectId}/scenes`),
    create: (
      projectId: string,
      body: { heading?: string; logline?: string; script_text?: string; after_scene_id?: string },
    ) => post<Scene>(`/projects/${projectId}/scenes`, body),
    update: (id: string, body: ScenePatch) => patch<Scene>(`/scenes/${id}`, body),
    reorder: (projectId: string, sceneIds: string[]) =>
      post<Scene[]>(`/projects/${projectId}/scenes/reorder`, { scene_ids: sceneIds }),
    remove: (id: string) => del(`/scenes/${id}`),
  },
  characters: {
    list: (projectId: string) => get<Character[]>(`/projects/${projectId}/characters`),
    create: (projectId: string, body: { name: string; description?: string }) =>
      post<Character>(`/projects/${projectId}/characters`, body),
    update: (id: string, body: Partial<Pick<Character, 'name' | 'description' | 'locked' | 'profile' | 'voice_id'>>) =>
      patch<Character>(`/characters/${id}`, body),
    // from a film (projectId) a studio character is only unlinked; without it, it goes everywhere
    remove: (id: string, projectId?: string) =>
      del(`/characters/${id}${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
    // contract-v25: Character Studio
    studio: (q?: string) => get<Character[]>(`/characters${q?.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`),
    get: (id: string) => get<Character>(`/characters/${id}`),
    createStudio: (body: { name: string; description?: string; profile?: CharacterProfile }) =>
      post<Character>('/characters', body),
    fromPhoto: (body: { name: string; media_id: string; description?: string; consent: PhotoConsentRecord; use?: 'studio' | 'as_is' }) =>
      post<Character>('/characters/from-photo', body),
    // own images: an uploaded picture used as it is (consent only for a photo of a real person)
    uploadPortrait: (id: string, body: { media_id: string; consent?: PhotoConsentRecord }) =>
      post<Generation>(`/characters/${id}/portrait/upload`, body),
    uploadPanel: (id: string, key: string, body: { media_id: string; consent?: PhotoConsentRecord }) =>
      post<Generation>(`/characters/${id}/sheet/panels/${encodeURIComponent(key)}/upload`, body),
    uploadSheet: (id: string, body: { media_id: string; consent?: PhotoConsentRecord }) =>
      post<CharacterSheet>(`/characters/${id}/sheet/upload`, body),
    cutSheet: (id: string, crops: SheetCrop[]) => post<CharacterSheet>(`/characters/${id}/sheet/crops`, { crops }),
    saveToStudio: (id: string) => post<Character>(`/characters/${id}/save-to-studio`),
    link: (projectId: string, characterId: string) =>
      post<Character>(`/projects/${projectId}/cast`, { character_id: characterId }),
    aiProfile: (id: string) => post<Job>(`/characters/${id}/ai/profile`),
    sheet: (id: string) => get<CharacterSheet>(`/characters/${id}/sheet`),
    makeSheet: (id: string, body: SheetRequest) => post<CharacterSheet>(`/characters/${id}/sheet`, body),
    patchSheet: (id: string, body: SheetTextPatch) => patch<CharacterSheet>(`/characters/${id}/sheet`, body),
    regeneratePanel: (id: string, key: string, note?: string) =>
      post<Generation>(`/characters/${id}/sheet/panels/${encodeURIComponent(key)}/regenerate`, { note: note ?? '' }),
    makeBoard: (id: string, layout: BoardLayout) => post<Job>(`/characters/${id}/sheet/board`, { layout }),
    catalogue: () => get<SheetCatalogue>('/character-sheet/catalogue'),
    boardUrl: (id: string, format: 'png' | 'pdf') => `${API_BASE}/characters/${id}/sheet/board/download?format=${format}`,
    packUrl: (id: string) => `${API_BASE}/characters/${id}/pack`,
    // contract-v26-character-voices.md
    designVoice: (id: string, body: { extra?: string; language?: string; name?: string } = {}) =>
      post<CharacterVoiceDesign>(`/characters/${id}/voice/design`, body),
    testClip: (id: string) => get<TestClip>(`/characters/${id}/test-clip`),
    makeTestClip: (id: string, text: string) => post<TestClip>(`/characters/${id}/test-clip`, { text }),
  },
  locations: {
    list: (projectId: string) => get<Location[]>(`/projects/${projectId}/locations`),
    create: (projectId: string, body: { name: string; description?: string }) =>
      post<Location>(`/projects/${projectId}/locations`, body),
    update: (id: string, body: Partial<Pick<Location, 'name' | 'description' | 'locked' | 'time_of_day_variants'>>) =>
      patch<Location>(`/locations/${id}`, body),
    remove: (id: string) => del(`/locations/${id}`),
  },
  shots: {
    list: (projectId: string) => get<Shot[]>(`/projects/${projectId}/shots`),
    // contract-v25: the pictures its keyframes are drawn with, and the panels each character could use
    refs: (id: string) => get<{ max_refs: number; refs: ShotRef[] }>(`/shots/${id}/refs`),
    listForScene: (sceneId: string) => get<Shot[]>(`/scenes/${sceneId}/shots`),
    create: (
      sceneId: string,
      body: { shot_type?: ShotType; duration_s?: number; description?: string; after_shot_id?: string } = {},
    ) => post<Shot>(`/scenes/${sceneId}/shots`, body),
    update: (id: string, body: ShotPatch) => patch<Shot>(`/shots/${id}`, body),
    reorder: (sceneId: string, shotIds: string[]) => post<Shot[]>(`/scenes/${sceneId}/shots/reorder`, { shot_ids: shotIds }),
    remove: (id: string) => del(`/shots/${id}`),
    clearStale: (id: string) => post<Shot>(`/shots/${id}/clear-stale`),
    renderTakes: (id: string, { count, duration_s }: TakesRequest = {}) =>
      post<Job[]>(`/shots/${id}/takes`, {
        ...(count ? { count } : {}),
        ...(duration_s ? { duration_s } : {}),
      }),
    estimate: (id: string, durationS?: number) => get<ShotEstimate>(`/shots/${id}/estimate`, { duration_s: durationS }),
    renderScene: (sceneId: string, count?: number) => post<Job[]>(`/scenes/${sceneId}/render`, count ? { count } : {}),
    // UI polish P3
    merge: (shotIds: string[]) => post<Shot[]>('/shots/merge', { shot_ids: shotIds }),
    split: (id: string, body: { at_ratio?: number; descriptions?: [string, string] } = {}) =>
      post<Shot[]>(`/shots/${id}/split`, body),
    extend: (id: string, body: { duration_s?: number; prompt?: string } = {}) =>
      post<ShotExtendResult>(`/shots/${id}/extend`, body),
    rerender: (id: string, body: { what: 'frames' | 'takes'; count?: number; params?: Record<string, unknown> }) =>
      post<ShotRerenderResult>(`/shots/${id}/rerender`, body),
    setReview: (sceneId: string, status: 'pending' | null) => patch<Scene>(`/scenes/${sceneId}/shots-review`, { status }),
    approveShotList: (sceneId: string, params?: Record<string, unknown>) =>
      post<ShotsApproveResult>(`/scenes/${sceneId}/shots-review/approve`, { generate_frames: true, ...(params ? { params } : {}) }),
  },
  storyboard: {
    fromScript: (projectId: string, body: StoryboardRequest) => post<Job>(`/projects/${projectId}/storyboard`, body),
  },
  generations: {
    list: (q: GenerationQuery) => get<Generation[]>('/generations', { ...q }),
    create: (body: {
      target_type: TargetType
      target_id: string
      kind: GenerationKind
      prompt: string
      params?: Record<string, unknown>
    }) => post<Generation>('/generations', body),
    regenerate: (
      id: string,
      body: { mode: RegenerateMode; note?: string; prompt?: string; params?: Record<string, unknown> },
    ) => post<Generation>(`/generations/${id}/regenerate`, body),
    approve: (id: string) => post<Generation>(`/generations/${id}/approve`),
    unapprove: (id: string) => post<Generation>(`/generations/${id}/unapprove`),
    reject: (id: string, reason?: string) => post<Generation>(`/generations/${id}/reject`, reason ? { reason } : {}),
    restore: (id: string) => post<Generation>(`/generations/${id}/restore`),
    regenerateChunk: (id: string, idx: number, body: { prompt?: string; seed?: number } = {}) =>
      post<Job>(`/generations/${id}/chunks/${idx}/regenerate`, body),
    upscale: (id: string, body: UpscaleRequest | ImageUpscaleRequest) => post<Job>(`/generations/${id}/upscale`, body),
    get: (id: string) => get<Generation>(`/generations/${id}`),
  },
  reel: {
    get: (projectId: string) => get<Reel>(`/projects/${projectId}/reel`),
    sync: (projectId: string) => post<Reel>(`/projects/${projectId}/reel/sync`),
    updateClip: (id: string, body: ReelClipPatch) => patch<ReelClip>(`/reel-clips/${id}`, body),
    reorder: (projectId: string, clipIds: string[]) => post<Reel>(`/projects/${projectId}/reel/reorder`, { clip_ids: clipIds }),
    assemble: (projectId: string, body: StitchRequest = { quality: 'draft' }) =>
      post<Job>(`/projects/${projectId}/reel/assemble`, body),
    renders: (projectId: string) => get<Render[]>(`/projects/${projectId}/renders`),
    rename: (renderId: string, title: string) => patch<Render>(`/generations/${renderId}`, { title }),
    // a plain link: the browser streams the file and names it from Content-Disposition
    downloadUrl: (generationId: string) => `${API_BASE}/generations/${generationId}/download`,
    estimate: (projectId: string) => get<ReelEstimate>(`/projects/${projectId}/reel/estimate`),
  },
  ai: {
    outline: (projectId: string) => post<Job>(`/projects/${projectId}/ai/outline`),
    writeMissing: (projectId: string, body: { scene_ids?: string[]; after_scene_id?: string; count?: number } = {}) =>
      post<Job>(`/projects/${projectId}/ai/write-missing`, body),
    continueStory: (projectId: string, count = 1) => post<Job>(`/projects/${projectId}/ai/continue`, { count }),
    extractCharacters: (projectId: string) => post<Job>(`/projects/${projectId}/ai/extract-characters`),
    assist: (sceneId: string, body: { action: AssistAction; idea?: string; tone?: string }) =>
      post<Job>(`/scenes/${sceneId}/ai/assist`, body),
    extractLocations: (projectId: string) => post<Job>(`/projects/${projectId}/ai/extract-locations`),
    suggestShots: (sceneId: string, maxShots?: number, reviewFirst?: boolean) =>
      post<Job>(`/scenes/${sceneId}/ai/suggest-shots`, {
        ...(maxShots ? { max_shots: maxShots } : {}),
        ...(reviewFirst ? { review_first: true } : {}),
      }),
    compilePrompts: (shotId: string) => post<Job>(`/shots/${shotId}/ai/compile-prompts`),
    beats: (shotId: string) => post<Job>(`/shots/${shotId}/ai/beats`),
    portraitPrompt: (characterId: string) => post<Job>(`/characters/${characterId}/ai/portrait-prompt`),
    suggestions: (projectId: string) => get<Suggestion[]>(`/projects/${projectId}/suggestions`, { status: 'pending' }),
    accept: (id: string) => post<SuggestionResult>(`/suggestions/${id}/accept`),
    reject: (id: string) => post<SuggestionResult>(`/suggestions/${id}/reject`),
  },
  quick: {
    create: (body: QuickRequest) => post<{ project: Project; job: Job }>('/quick', body),
    recent: () => get<QuickRecent[]>('/quick/recent'),
  },
  // contract-v5: the Library behind the Image and Video sections
  media: {
    list: (q: MediaQuery = {}) => get<MediaPage>('/media', { ...q }),
    get: (id: string) => get<MediaDetail>(`/media/${id}`),
    update: (id: string, body: { title?: string; tags?: string[] }) => patch<MediaItem>(`/media/${id}`, body),
    remove: (id: string) => del(`/media/${id}`),
    move: (refs: string[], folderId: string | null) => post<BatchResult>('/media/move', { refs, folder_id: folderId }),
    batch: (action: BatchAction, refs: string[], options: Record<string, unknown> = {}) => post<BatchResult>('/media/batch', { action, refs, options }),
    regenerate: (id: string, body: { mode: RegenerateMode; note?: string; prompt?: string }) =>
      post<Job>(`/media/${id}/regenerate`, body),
    // upload goes through lib/upload.ts (XHR, for progress)
  },
  photos: {
    list: (q: Record<string, string | number | boolean | undefined>) => get<PhotoPage>('/photos', q),
    facets: (q: Record<string, string | number | boolean | undefined>) => get<Facets>('/photos/facets', q),
    neighbours: (id: string, q: Record<string, string | number | boolean | undefined>) =>
      get<{ ids: string[]; index: number | null; position?: number; total: number; prev?: string | null; next?: string | null }>(`/photos/${id}/neighbours`, q),
    marks: (body: { ids: string[]; rating?: number; flag?: 'pick' | 'reject' | 'none'; label?: string }) =>
      post<{ updated: number }>('/photos/marks', body),
    update: (id: string, body: { title?: string; caption?: string; keywords?: string[] }) => patch<MediaItem>(`/photos/${id}`, body),
    exif: (id: string) => get<PhotoExif>(`/photos/${id}/exif`),
    sourceUrl: (id: string) => `${API_BASE}/photos/${id}/source`,
  },
  albums: {
    list: () => get<Album[]>('/albums'),
    create: (body: Partial<Album> & { name: string; kind: Album['kind'] }) => post<Album>('/albums', body),
    update: (id: string, body: Partial<Album>) => patch<Album>(`/albums/${id}`, body),
    remove: (id: string) => del(`/albums/${id}`),
    items: (id: string, ids: string[], action: 'add' | 'remove' = 'add') =>
      post<{ changed: number; album: Album }>(`/albums/${id}/items`, { ids, action }),
    order: (id: string, ids: string[]) => post<void>(`/albums/${id}/order`, { ids }),
  },
  clients: {
    list: () => get<Client[]>('/clients'),
    create: (body: Pick<Client, 'name'> & Partial<Pick<Client, 'email' | 'phone' | 'notes'>>) => post<Client>('/clients', body),
    update: (id: string, body: Partial<Pick<Client, 'name' | 'email' | 'phone' | 'notes'>>) => patch<Client>(`/clients/${id}`, body),
    remove: (id: string) => del(`/clients/${id}`),
  },
  upscales: {
    list: (q: { kind?: MediaKind; limit?: number; cursor?: string }) => get<UpscalePage>('/upscales', { ...q }),
    remove: (generationId: string) => del(`/upscales/${generationId}`),
  },
  images: {
    generate: (body: ImageGenerateRequest) => post<MediaBatch>('/images/generate', body),
    edit: (body: ImageEditRequest) => post<MediaBatch>('/images/edit', body),
    // contract v35: a brand card, on the user's photo or on a scene made first
    brand: (body: BrandImageRequest) => post<MediaBatch>('/images/brand', body),
    img2img: (body: Img2ImgRequest) => post<MediaBatch>('/images/img2img', body),
    tools: () => get<{ tools: ImageToolInfo[]; control?: ControlStatus }>('/images/tools'),
    // contract v31
    options: () => get<ImageOptions>('/images/options'),
    control: (body: ImageControlRequest) => post<MediaBatch>('/images/control', body),
  },
  // contract-v6
  models: {
    list: (type: ModelType) => get<ModelInfo[]>('/models', { type }),
  },
  // contract-v23
  text: {
    aerab: (body: TextAerabRequest) => post<TextAerabResponse>('/text/aerab', body),
  },
  // contract-v14
  audio: {
    generate: (body: AudioGenerateRequest) => post<MediaBatch>('/audio/generate', body),
    lyrics: (body: LyricsRequest) => post<LyricsResponse>('/audio/lyrics', body),
    aerab: (lyrics: string, language?: string) => post<AerabResponse>('/audio/lyrics/aerab', { lyrics, ...(language ? { language } : {}) }),
    // contract-v15
    peaks: (id: string, q: { n?: number; version?: string } = {}) => get<Peaks>(`/audio/${id}/peaks`, q),
    analysis: (id: string, version?: string) => get<AudioAnalysis>(`/audio/${id}/analysis`, { version }),
    preview: async (id: string, body: { edit: AudioEdit; version?: string }, signal?: AbortSignal) =>
      audioPreview(await send(`/audio/${id}/preview`, jsonInit(body, signal))),
    render: (id: string, body: { edit: AudioEdit; version?: string; note?: string }) => post<{ item: MediaItem; job: Job }>(`/audio/${id}/render`, body),
    exportUrl: (id: string, q: { format: AudioExportFormat; loudness?: LoudnessTarget | null; version?: string }) =>
      API_BASE + withQuery(`/audio/${id}/export`, q),
    mixPreview: async (body: MixIn, signal?: AbortSignal) => audioPreview(await send('/audio/mix/preview', jsonInit(body, signal))),
    mix: (body: MixIn & { stems?: boolean; note?: string }) => post<{ item: MediaItem; job: Job }>('/audio/mix', body),
    // contract-v16
    speak: (body: SpeakRequest) => post<SpeakResponse>('/audio/speak', body),
    songVoices: () => get<{ available: boolean; vocals: string[] }>('/audio/song-voices'),
    // contract-v21
    stems: (id: string, body: { mode: StemsMode; version?: string; lyrics?: boolean }) => post<{ job: Job; items: MediaItem[] }>(`/audio/${id}/stems`, body),
    stemsOf: (id: string) => get<StemsResponse>(`/audio/${id}/stems`),
  },
  voices: {
    list: async (q: VoiceQuery = {}) => listOf(await get<Voice[] | { items: Voice[] }>('/voices', { ...q, starred: q.starred || undefined })),
    engines: async () => listOf(await get<VoiceEngine[] | { items: VoiceEngine[] }>('/voices/engines')),
    create: (body: VoiceCreate) => post<Voice>('/voices', body),
    update: (id: string, body: VoicePatch) => patch<Voice>(`/voices/${id}`, body),
    remove: (id: string) => del(`/voices/${id}`),
    transcribe: async (id: string) => {
      const res = await post<Job | { job: Job }>(`/voices/${id}/transcribe`)
      return 'job' in res ? res.job : res
    },
    designPreview: (body: DesignPreviewRequest) => post<DesignPreviewResponse>('/voices/design/preview', body),
    // contract-v19
    gallery: (q: GalleryQuery = {}) => get<GalleryResponse>('/voices/gallery', q),
    gallerySample: async (id: string): Promise<GallerySample> => {
      const path = `/voices/gallery/${encodeURIComponent(id)}/sample`
      let res: Response
      try {
        res = await fetch(API_BASE + path, { credentials: 'include' })
      } catch {
        throw new ApiError(0, UNREACHABLE)
      }
      if (res.status === 202) {
        const body = (await res.json()) as { job: Job }
        return { ready: false, job: body.job }
      }
      if (!res.ok) throw new ApiError(res.status, messageFrom(res.status, await res.json().catch(() => null)))
      // the player fetches it again (from the cache); only the status mattered here
      res.body?.cancel().catch(() => {})
      return { ready: true, url: API_BASE + path }
    },
    galleryAdd: (id: string, body: { starred?: boolean } = {}) => post<Voice>(`/voices/gallery/${encodeURIComponent(id)}/add`, body),
  },
  // contract-v17
  scripts: {
    list: async () => listOf(await get<ScriptSummary[] | { items: ScriptSummary[] }>('/scripts')),
    get: (id: string) => get<Script>(`/scripts/${id}`),
    create: (body: ScriptCreate) => post<Script>('/scripts', body),
    update: (id: string, body: ScriptPatch) => patch<Script>(`/scripts/${id}`, body),
    remove: (id: string) => del(`/scripts/${id}`),
    split: (id: string, body: { text: string; mode: SplitMode; speaker_id?: string | null }) => post<Script>(`/scripts/${id}/split`, body),
    importProject: (id: string, body: { project_id: string; scene_ids?: string[]; include_action?: boolean }) =>
      post<Script>(`/scripts/${id}/import-project`, body),
    render: (id: string, lineIds?: string[]) => post<RenderResponse>(`/scripts/${id}/render`, lineIds ? { line_ids: lineIds } : {}),
    mix: (id: string) => post<Job>(`/scripts/${id}/mix`),
    stems: (id: string) => post<Job>(`/scripts/${id}/stems`),
  },
  // contract-v18
  dubs: {
    list: async () => listOf(await get<DubSummary[] | { items: DubSummary[] }>('/dubs')),
    get: (id: string) => get<Dub>(`/dubs/${id}`),
    create: (body: DubCreate) => post<Dub>('/dubs', body),
    update: (id: string, body: DubPatch) => patch<Dub>(`/dubs/${id}`, body),
    remove: (id: string) => del(`/dubs/${id}`),
    continue: (id: string) => post<Dub>(`/dubs/${id}/continue`),
    regen: (id: string, body: { lang: string; segment_ids?: string[] }) => post<Dub>(`/dubs/${id}/regen`, body),
    split: (id: string, body: { segment_id: string; at: number }) => post<Dub>(`/dubs/${id}/segments/split`, body),
    merge: (id: string, segmentIds: string[]) => post<Dub>(`/dubs/${id}/segments/merge`, { segment_ids: segmentIds }),
    export: (id: string, body: DubExportBody) => post<{ job: Job; item_id: string; generation_id: string }>(`/dubs/${id}/export`, body),
  },
  transcripts: {
    start: (body: { media_id: string; language?: string; words?: boolean; cleanup?: boolean }) =>
      post<{ transcript_id: string; job: Job }>('/transcribe', body),
    list: async (mediaId?: string) => listOf(await get<TranscriptSummary[] | { items: TranscriptSummary[] }>('/transcripts', { media_id: mediaId })),
    get: (id: string) => get<Transcript>(`/transcripts/${id}`),
    remove: (id: string) => del(`/transcripts/${id}`),
    cleanup: (id: string) => post<Job>(`/transcripts/${id}/cleanup`),
    // contract-v23: the tidied lines, one per segment ("Add aerab" saves through here)
    setClean: (id: string, lines: string[]) => request<Transcript>('PUT', `/transcripts/${id}/clean`, { lines }),
    video: (id: string, body: { mode: 'soft' | 'burn'; clean?: boolean }) =>
      post<{ job: Job; item_id: string; generation_id: string }>(`/transcripts/${id}/video`, body),
  },
  // contract-v20
  podcast: {
    shows: () => get<Show[]>('/podcast/shows'),
    createShow: (body: ShowCreate) => post<Show>('/podcast/shows', body),
    updateShow: (id: string, body: ShowPatch) => patch<Show>(`/podcast/shows/${id}`, body),
    removeShow: (id: string) => del(`/podcast/shows/${id}`),
    addAd: (id: string, body: Omit<Ad, 'id'>) => post<Show>(`/podcast/shows/${id}/ads`, body),
    removeAd: (id: string, adId: string) => request<Show>('DELETE', `/podcast/shows/${id}/ads/${adId}`),
    episodes: (showId?: string) => get<EpisodeSummary[]>('/podcast/episodes', { show_id: showId }),
    get: (id: string) => get<Episode>(`/podcast/episodes/${id}`),
    create: (body: EpisodeCreate) => post<Episode>('/podcast/episodes', body),
    update: (id: string, body: EpisodePatch) => patch<Episode>(`/podcast/episodes/${id}`, body),
    remove: (id: string) => del(`/podcast/episodes/${id}`),
    addSource: async (id: string, file: File) => {
      const form = new FormData()
      form.append('file', file, file.name)
      return (await (await send(`/podcast/episodes/${id}/sources`, { method: 'POST', body: form })).json()) as Episode
    },
    removeSource: (id: string, docId: string) => request<Episode>('DELETE', `/podcast/episodes/${id}/sources/${docId}`),
    pasteScript: (id: string, text: string) => post<Episode>(`/podcast/episodes/${id}/script`, { text }),
    write: (id: string) => post<Job>(`/podcast/episodes/${id}/write`),
    editLines: (id: string, body: { op: EditOp; line_ids?: string[]; chapter_id?: string; note?: string }) =>
      post<Job>(`/podcast/episodes/${id}/edit-lines`, body),
    render: (id: string, lineIds?: string[]) => post<RenderResponse>(`/podcast/episodes/${id}/render`, lineIds ? { line_ids: lineIds } : {}),
    recordLine: (id: string, body: { line_id: string; media_id: string }) => post<Episode>(`/podcast/episodes/${id}/record-line`, body),
    insert: (id: string, body: InsertBody) => post<Episode>(`/podcast/episodes/${id}/insert`, body),
    mix: (id: string) => post<Job>(`/podcast/episodes/${id}/mix`),
    notes: (id: string) => post<Job>(`/podcast/episodes/${id}/notes`),
    publish: (id: string, body: PublishBody) => post<Job>(`/podcast/episodes/${id}/publish`, body),
    video: (id: string, body: VideoBody) => post<Job>(`/podcast/episodes/${id}/video`, body),
    srtUrl: (id: string) => `${API_BASE}/podcast/episodes/${id}/subtitles.srt`,
    notesUrl: (id: string) => `${API_BASE}/podcast/episodes/${id}/notes.md`,
  },
  // contract-v24: the AI video podcast (the Audiogram stays podcast.video)
  aiVideo: {
    getEpisodeAiVideo: (episodeId: string) => get<VideoPodcast>(`/podcast/episodes/${episodeId}/ai-video`),
    createEpisodeAiVideo: (episodeId: string, body: { aspect?: string } = {}) => post<VideoPodcast>(`/podcast/episodes/${episodeId}/ai-video`, body),
    getAiVideo: (id: string) => get<VideoPodcast>(`/podcast/ai-video/${id}`),
    patchAiVideo: (id: string, body: VideoPodcastPatch) => patch<VideoPodcast>(`/podcast/ai-video/${id}`, body),
    deleteAiVideo: (id: string) => del(`/podcast/ai-video/${id}`),
    continueAiVideo: (id: string) => post<Job>(`/podcast/ai-video/${id}/continue`),
    regenerateAiVideo: (id: string, body: VPRegenerate) => post<Generation>(`/podcast/ai-video/${id}/regenerate`, body),
    aiVideoCharacters: (id: string) => get<VPCharacterOption[]>(`/podcast/ai-video/${id}/characters`),
    replanAiVideo: (id: string) => post<VideoPodcast>(`/podcast/ai-video/${id}/replan`),
    switchAiVideoCut: (id: string, body: { at: number; camera: string }) => post<VideoPodcast>(`/podcast/ai-video/${id}/cuts/switch`, body),
    patchAiVideoCut: (id: string, cutId: string, body: { camera: string }) => patch<VideoPodcast>(`/podcast/ai-video/${id}/cuts/${cutId}`, body),
    deleteAiVideoCut: (id: string, cutId: string) => request<VideoPodcast>('DELETE', `/podcast/ai-video/${id}/cuts/${cutId}`),
    aiVideoEstimate: (id: string, quality: VPQuality, trackIds?: string[]) =>
      get<VPEstimate>(`/podcast/ai-video/${id}/estimate`, { quality, track_ids: trackIds?.length ? trackIds.join(',') : undefined }),
    renderAiVideo: (id: string, body: { quality: VPQuality; track_ids?: string[]; from_piece?: number }) => post<Job>(`/podcast/ai-video/${id}/render`, body),
    assembleAiVideo: (id: string, quality: VPQuality) => post<Job>(`/podcast/ai-video/${id}/assemble`, { quality }),
  },
  videos: {
    // a MediaItem plus its job; tolerate a batch too
    generate: (body: VideoGenerateRequest) => post<(MediaItem & { job?: Job | null }) | MediaBatch>('/videos/generate', body),
  },
  // contract-v8 P2
  mentions: {
    search: (q: { q?: string; project_id?: string; types?: string; kit_id?: string }) => get<MentionOption[]>('/mentions', q),
  },
  camera: {
    presets: () => get<CameraPresets>('/camera/presets'),
  },
  // contract-v7
  brandKits: {
    list: () => get<BrandKit[]>('/brand-kits'),
    get: (id: string) => get<BrandKit>(`/brand-kits/${id}`),
    create: (body: BrandKitPatch & { name: string; is_default?: boolean }) => post<BrandKit>('/brand-kits', body),
    update: (id: string, body: BrandKitPatch) => patch<BrandKit>(`/brand-kits/${id}`, body),
    remove: (id: string) => del(`/brand-kits/${id}`),
    setDefault: (id: string) => post<BrandKit>(`/brand-kits/${id}/default`),
    // synchronous PNG; settings carry the editor's unsaved changes
    preview: (id: string, body: { kind: PreviewKind; sample_media_id?: string; settings?: unknown }) => postBlob(`/brand-kits/${id}/preview`, body),
    logoReveal: (id: string, body: LogoRevealRequest) => post<{ item: MediaItem; job: Job }>(`/brand-kits/${id}/logo-reveal`, body),
  },
  templates: {
    list: (type: TemplateType) => get<Template[]>('/prompt-templates', { type }),
    start: (id: string) => post<TemplateStart>(`/prompt-templates/${id}/start`),
    // v31: the workspace's own ("Save as template")
    save: (body: UserTemplateRequest) => post<Template>('/my-templates', body),
    rename: (id: string, title: string) => patch<Template>(`/my-templates/${id}`, { title }),
    remove: (id: string) => del(`/my-templates/${id}`),
  },
  // contract-v9: Photo Studio
  photo: {
    schema: () => get<PhotoSchema>('/photo/schema'),
    preview: async (id: string, params: DevelopParams, maxSide = 1280, signal?: AbortSignal, debug?: 'sharpen_mask' | null): Promise<PreviewResult> => {
      const res = await send(`/photo/${id}/preview`, jsonInit({ params, max_side: maxSide, ...(debug ? { debug } : {}) }, signal))
      const ms = Number(res.headers.get('X-Develop-Ms'))
      return { url: URL.createObjectURL(await res.blob()), ms: Number.isFinite(ms) && ms > 0 ? ms : null, outputSize: res.headers.get('X-Output-Size') }
    },
    // contract-v22: measured on the picture, not saved
    upright: (id: string, mode: UprightMode) => post<UprightResult>(`/photo/${id}/upright`, { mode }),
    lensCa: (id: string) => post<{ caRed: number; caBlue: number; edges: number }>(`/photo/${id}/lens/ca`),
    histogram: (id: string, params: DevelopParams) => post<Histogram>(`/photo/${id}/histogram`, { params }),
    auto: (id: string) => post<AutoResult>(`/photo/${id}/auto`),
    palette: (id: string, k = 8) => get<{ clusters: NonNullable<DevelopParams['palette']> }>(`/photo/${id}/palette`, { k }),
    render: (id: string, body: { params: DevelopParams; format?: ExportFormat; quality?: number; note?: string }) =>
      post<Job>(`/photo/${id}/render`, body),
    history: (id: string) => get<PhotoHistory>(`/photo/${id}/history`),
    revert: (id: string) => post<PhotoHistory>(`/photo/${id}/revert`),
    cube: async (params: DevelopParams, title: string, size = 33) => (await send('/photo/cube', jsonInit({ params, size, title }))).blob(),
    tools: () => get<{ tools: RestoreTool[]; effects: EffectSpec[] }>('/photo/tools'),
    smartPlan: (id: string) => get<SmartPlan>(`/photo/${id}/smart-plan`),
    smartRestore: (id: string, steps: Pick<PlanStep, 'tool' | 'on' | 'variant' | 'strength'>[]) =>
      post<{ chain_id: string; steps: { tool: string; label: string }[]; first_job_id: string }>(`/photo/${id}/smart-restore`, { steps }),
    restore: (id: string, body: RestoreRequest) => post<Job>(`/photo/${id}/restore`, body),
    effect: (id: string, name: string, strength = 1) => post<Job>(`/photo/${id}/effect`, { name, strength, format: 'png' }),
    background: (id: string, background: Background, edge?: Edge) => post<Job>(`/photo/${id}/background`, { background, edge }),
    maskUrl: (id: string) => `${API_BASE}/photo/${id}/mask`,
    describe: (id: string) => post<Described>(`/photo/${id}/describe`),
    snapshots: (id: string) => get<Snapshot[]>(`/photo/${id}/snapshots`),
    createSnapshot: (id: string, body: { name: string; params: DevelopParams; base_id?: string }) => post<Snapshot>(`/photo/${id}/snapshots`, body),
    updateSnapshot: (sid: string, body: { name?: string; params?: DevelopParams }) => patch<Snapshot>(`/photo/snapshots/${sid}`, body),
    deleteSnapshot: (sid: string) => del(`/photo/snapshots/${sid}`),
    profiles: () => get<ProfileInfo[]>('/photo/profiles'),
    // a 3D table for the WebGL preview: size³ RGB bytes, red fastest
    lut: async (kind: 'profile' | 'look', id: string) => {
      const t = await get<{ size: number; data: string }>(`/photo/luts/${kind}/${encodeURIComponent(id)}`)
      return { size: t.size, data: Uint8Array.from(atob(t.data), (c) => c.charCodeAt(0)) }
    },
    sync: (body: { ids: string[]; params: DevelopParams; groups: string[]; format?: ExportFormat }) =>
      post<{ queued: number; results: { id: string; job_id: string | null; error: string | null; unchanged?: boolean }[] }>('/photo/sync', body),
  },
  looks: {
    list: () => get<Look[]>('/looks'),
    create: (body: { name: string; params: DevelopParams; description?: string; category?: string; generation_id?: string }) =>
      post<Look>('/looks', body),
    update: (id: string, body: { name?: string; params?: DevelopParams; description?: string; category?: string }) => patch<Look>(`/looks/${id}`, body),
    remove: (id: string) => del(`/looks/${id}`),
    importFiles: async (files: File[]): Promise<ImportResult> => {
      const form = new FormData()
      files.forEach((f) => form.append('files', f, f.name))
      try {
        return (await (await send('/looks/import', { method: 'POST', body: form })).json()) as ImportResult
      } catch (e) {
        // a 422 still carries the per-file reports
        const detail = (e as ApiError).detail as Partial<ImportResult> | { detail?: Partial<ImportResult> } | undefined
        const reports = (detail as Partial<ImportResult>)?.reports ?? (detail as { detail?: Partial<ImportResult> })?.detail?.reports
        if (e instanceof ApiError && e.status === 422 && reports) return { looks: [], reports }
        throw e
      }
    },
    cubeUrl: (id: string, size = 33) => `${API_BASE}/looks/${id}/cube?size=${size}`,
    thumbUrl: (id: string, generationId?: string) =>
      `${API_BASE}/looks/${id}/thumb${generationId ? `?generation_id=${encodeURIComponent(generationId)}` : ''}`,
    applyVideo: (id: string, body: { generation_id: string; intensity: number }) => post<Job>(`/looks/${id}/apply-video`, body),
  },
  dashboard: () => get<Dashboard>('/dashboard'),
  // contract-v8 P4: library organisation
  folders: {
    list: (kind?: MediaKind) => get<Folder[]>('/folders', kind ? { kind } : undefined),
    create: (body: { name: string; parent_id?: string | null; kind?: FolderKind }) => post<Folder>('/folders', body),
    update: (id: string, body: { name?: string; parent_id?: string; sort?: number }) => patch<Folder>(`/folders/${id}`, body),
    remove: (id: string) => del(`/folders/${id}`),
  },
  favourites: {
    list: () => get<{ refs: string[] }>('/favourites'),
    toggle: (ref: string, on?: boolean) => post<{ ref: string; favourite: boolean }>('/favourites/toggle', on == null ? { ref } : { ref, on }),
    import: (refs: string[]) => post<{ refs: string[]; imported: number; skipped: number }>('/favourites/import', { refs }),
  },
  savedFilters: {
    list: () => get<SavedFilter[]>('/saved-filters'),
    create: (body: { name: string; query: Record<string, unknown> }) => post<SavedFilter>('/saved-filters', body),
    remove: (id: string) => del(`/saved-filters/${id}`),
  },
  exportUrl: (jobId: string) => `${API_BASE}/exports/${jobId}/download`,
  // P1 polish; both answer 404 on servers that predate them
  prompts: {
    enhance: (body: PromptEnhanceRequest) => post<PromptEnhanceResult>('/prompts/enhance', body),
  },
  estimate: (q: EstimateQuery) => get<EstimateResult>('/estimate', { ...q }),
  // contract v40: 3D Studio
  models3d: {
    make: (body: Make3DRequest) => post<Make3DResponse>('/3d/make', body),
    turntable: async (id: string, video: Blob) => {
      const form = new FormData()
      form.append('file', video, video.type.includes('mp4') ? 'turntable.mp4' : 'turntable.webm')
      return (await (await send(`/3d/${id}/turntable`, { method: 'POST', body: form })).json()) as Turntable3DResult
    },
    thumbnail: async (id: string, picture: Blob) => {
      const form = new FormData()
      form.append('file', picture, 'thumbnail.png')
      return (await (await send(`/3d/${id}/thumbnail`, { method: 'POST', body: form })).json()) as MediaItem
    },
    // each file is named after its angle (front.png), which is how the server tells them apart
    shots: async (id: string, shots: { file: string; blob: Blob }[]) => {
      const form = new FormData()
      for (const s of shots) form.append('file', s.blob, s.file)
      return (await (await send(`/3d/${id}/shots`, { method: 'POST', body: form })).json()) as Shots3DResult
    },
  },
  jobs: {
    list: (q?: { status?: JobStatus; project_id?: string; via?: 'api' }) => get<Job[]>('/jobs', q),
    cancel: (id: string) => post<Job>(`/jobs/${id}/cancel`),
    retry: (id: string) => post<Job>(`/jobs/${id}/retry`),
  },
}
