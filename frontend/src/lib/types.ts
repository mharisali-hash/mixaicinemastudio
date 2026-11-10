import type { CaptionStyle } from '@/components/generate/CaptionStylePicker'
// Mirrors docs/api/contract-v0.md. Keep snake_case so payloads pass through untouched.
import type { BrandPlacement } from './brand'
import type { CameraSetting } from './camera'

export type Role = 'owner' | 'editor' | 'viewer'

export interface User {
  id: string
  email: string
  display_name: string
  workspace_id: string
  role: Role
  // contract v27: Settings shows API tokens / the docs open for this user (admin only while PUBLIC_API is off)
  api_access?: boolean
  api_docs?: boolean
  // contract v34: the admin can list what was made through the API (Library and Queue)
  admin?: boolean
  // contract v38: false until the emailed link is opened; such a user can look around but not start work
  verified?: boolean
  // M22: Implant Preview (admins, or granted by one)
  implant?: boolean
  // contract v42: the staff role, and the hold a moderator put the account under (read-only while set)
  staff?: 'admin' | 'manager' | null
  blocked?: { reason: string; at: string } | null
}

// contract v38
export interface AuthConfig {
  signup: 'open' | 'invite' | 'closed'
  google: boolean
  verify: boolean
  // who confirms new accounts: a link by email, or an admin by hand when no email can be sent
  verify_by?: 'email' | 'admin'
}

export type AdminUserStatus = 'all' | 'active' | 'inactive' | 'unverified' | 'admin'

export interface AdminUser {
  id: string
  email: string
  display_name: string
  is_admin: boolean
  is_active: boolean
  verified: boolean
  workspace_id: string | null
  created_at: string
  last_login_at: string | null
  gpu_min_today: number
  gpu_min_month: number
  jobs: number
  tokens: number
  // contract v39: the package they're on now ("Unlimited" for admins)
  package_id?: string | null
  package_name?: string
  subscription_ends_at?: string | null
  gpu_min_day_limit?: number | null
  implant_access?: boolean
}

export interface AdminUserPatch {
  display_name?: string
  is_active?: boolean
  is_admin?: boolean
  verified?: boolean
  implant_access?: boolean
  // contract v39: null clears the override
  limit_gpu_min_day?: number | null
  limit_gpu_min_month?: number | null
  limit_max_queued?: number | null
}

// contract v39: packages, the GPU meter and the admin panel
export type PackagePeriod = 'day' | 'week' | 'month' | 'year' | 'none'
export type PackageKind = 'standard' | 'demo' | 'custom'
export type FeatureKey =
  | 'image' | 'video' | 'audio' | 'character' | 'ads' | 'poetry' | 'podcast' | 'dub' | 'stories' | '3d' | 'api'
  // contract v42
  | 'song_voices' | 'presenter' | 'print' | 'implant' | 'bulk_edit'

export interface Package {
  id: string
  slug: string | null
  name: string
  description: string
  price_pkr: number
  period: PackagePeriod
  days: number | null
  gpu_min_day: number | null
  gpu_min_month: number | null
  max_queued: number | null
  private_items: boolean
  models: string[] | null
  features: Partial<Record<FeatureKey, boolean>>
  kind: PackageKind
  public: boolean
  custom_for_user_id: string | null
  active: boolean
  sort: number
  created_at: string
  updated_at: string
  subscribers: number
}

export type PackageInput = Pick<
  Package,
  'name' | 'description' | 'price_pkr' | 'period' | 'days' | 'gpu_min_day' | 'gpu_min_month' | 'max_queued' | 'private_items' | 'models' | 'features' | 'kind' | 'public' | 'sort'
>

export type SubscriptionStatus = 'active' | 'expired' | 'cancelled' | 'pending_payment'
export type SubscriptionFilter = 'all' | 'active' | 'expiring' | 'expired' | 'cancelled' | 'pending_payment'

export interface Subscription {
  id: string
  user_id: string
  user_email: string
  package_id: string
  package_name: string
  starts_at: string
  ends_at: string | null
  status: SubscriptionStatus
  source: 'admin' | 'demo' | 'payment' | 'signup_default'
  notes: string
  granted_by: string | null
  created_at: string
}

export interface UsageGrant {
  id: string
  user_id: string
  gpu_min: number
  reason: string
  starts_at: string
  expires_at: string | null
  revoked_at: string | null
  granted_by: string | null
  created_at: string
  live: boolean
}

export type PaymentStatus = 'pending' | 'paid' | 'failed' | 'refunded'

export interface Payment {
  id: string
  user_id: string
  user_email: string
  subscription_id: string | null
  package_id: string | null
  package_name: string
  gateway: string
  amount_pkr: number
  status: PaymentStatus
  reference: string
  notes: string
  created_at: string
  updated_at: string
}

export interface PaymentDetail extends Payment {
  raw: Record<string, unknown> | null
}

export interface PaymentInput {
  user_id: string
  amount_pkr: number
  package_id?: string | null
  gateway?: string
  reference?: string
  status?: 'pending' | 'paid'
  notes?: string
}

export interface Usage {
  package: { id: string | null; name: string; kind: PackageKind | 'admin' }
  unlimited: boolean
  gpu_min_day: number | null
  gpu_min_month: number | null
  max_queued: number | null
  grant_min: number
  used_min_today: number
  used_min_month: number
  queued: number
  resets_at: string
  month_resets_at: string
  subscription: { id: string; status: SubscriptionStatus; starts_at: string; ends_at: string | null; source: string } | null
  features: Record<FeatureKey, boolean>
  models: string[] | null
}

// the flat 429 body when a package's limit is reached
export interface QuotaRefusal {
  detail: string
  reason: 'day' | 'month' | 'queued'
  used_min: number
  limit_min: number | null
  resets_at: string | null
  queued: number | null
  max_queued: number | null
}

export interface AdminUserDetail {
  user: AdminUser
  usage: Usage
  limits_override: { gpu_min_day: number | null; gpu_min_month: number | null; max_queued: number | null }
  subscription: Subscription | null
  subscriptions: Subscription[]
  grants: UsageGrant[]
  payments: Payment[]
  recent_jobs: { id: string; type: string; status: string; lane: string; via_api: boolean; created_at: string; gpu_min: number; error: string | null }[]
  had_demo: boolean
}

export interface AdminOverview {
  users: { total: number; new_week: number; active_today: number }
  subscriptions: { package_id: string; name: string; count: number }[]
  payments_month: { count: number; paid_pkr: number; pending: number }
  gpu_today: { user_id: string; email: string; display_name: string; gpu_min: number }[]
  queue: { queued: number; running: number; lanes: Record<string, { queued: number; running: number }> }
  workers: { live: number; stale: number; lanes: string[]; idle_lanes: string[] }
}

export interface UsageRow {
  day: string
  user_id: string | null
  email: string
  display_name: string
  gpu_min: number
  jobs: number
}

export interface UsageReport {
  days: number
  rows: UsageRow[]
  top: Omit<UsageRow, 'day'>[]
  total_min: number
}

export interface AuditRow {
  id: string
  actor_user_id: string | null
  actor_email: string
  action: string
  target_type: string
  target_id: string | null
  detail: Record<string, unknown>
  created_at: string
}

// contract v27
export type TokenScope = 'read' | 'generate' | 'manage'
export type TokenExpiry = 30 | 90 | 365 | null

export interface ApiToken {
  id: string
  name: string
  prefix: string
  scopes: TokenScope[]
  status: 'active' | 'expired' | 'revoked'
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApiTokenCreated extends ApiToken {
  token: string
}

export interface ApiTokenCreate {
  name: string
  scopes: TokenScope[]
  expires_days: TokenExpiry
}

export interface SystemStatus {
  comfy: {
    ok: boolean
    url: string
    version?: string
    devices?: { name: string; vram_total: number; vram_free: number }[]
    error?: string
  }
  llm: { ok: boolean; url: string; models?: string[]; error?: string }
  driver: 'mock' | 'comfy'
  worker: { alive: boolean; last_heartbeat?: string }
  // contract v38: everyone's, since the GPUs are shared
  queue?: { queued: number; running: number }
}

export type AuthoringMode = 'ai_director' | 'scene_by_scene' | 'import' | 'quick'
export type Quality = 'draft' | 'final'
export type ProjectStatus = 'draft' | 'in_progress' | 'rendering' | 'done'
export type AspectRatio = '16:9' | '9:16' | '1:1' | '2.39:1'

export interface Project {
  id: string
  title: string
  logline: string
  authoring_mode: AuthoringMode
  aspect_ratio: string
  target_runtime_s: number
  quality: Quality
  takes_per_shot: number
  overnight: boolean
  status: ProjectStatus
  created_at: string
  updated_at: string
  thumbnail_url?: string | null
  counts: { scenes: number; shots: number; characters: number }
  // contract-v6; older servers don't send it
  settings?: ProjectSettings
}

// PATCH merges these into project.settings; null removes a key
export interface ProjectSettings {
  image_model?: string | null
  image_speed?: string | null
  edit_model?: string | null
  video_quality?: 'standard' | 'hq' | null
  smooth_motion?: boolean | null
  lipsync_engine?: 'ltx' | 'wan_s2v' | null
  [key: string]: unknown
}

export interface ProjectCreate {
  title: string
  authoring_mode: AuthoringMode
  logline?: string
  aspect_ratio?: string
  target_runtime_s?: number
  quality?: Quality
  takes_per_shot?: number
  overnight?: boolean
  brief?: string
}

export type Source = 'user' | 'ai' | 'ai_edited'
export type TimeOfDay = 'dawn' | 'morning' | 'day' | 'golden_hour' | 'dusk' | 'night' | 'interior'

export interface Scene {
  id: string
  project_id: string
  order: number
  heading: string
  logline: string
  script_text: string
  summary: string
  time_of_day: TimeOfDay | null
  mood: string | null
  lighting: string | null
  source: Source
  locked: boolean
  version: number
  stale: boolean
  location_id?: string | null
  // P3 review gate: 'pending' while the planned shot list waits for approval; older servers omit it
  shots_review?: 'pending' | null
  created_at: string
  updated_at: string
}

export type ScenePatch = Partial<
  Pick<
    Scene,
    'heading' | 'logline' | 'script_text' | 'summary' | 'time_of_day' | 'mood' | 'lighting' | 'locked' | 'location_id'
  >
>

export type TargetType = 'character' | 'scene' | 'shot' | 'location' | 'project' | 'media'
export type GenerationKind =
  | 'portrait'
  | 'sheet_view'
  | 'establishing'
  | 'keyframe_start'
  | 'keyframe_end'
  | 'keyframe_mid'
  | 'take'
  | 'tile'
  | 'render'
  | 'mezzanine'
  | 'scene_text'
  // contract-v5: standalone media (Image studio results, uploads)
  | 'image'
  | 'video'
  | 'upload'
  // contract v40: 3D Studio
  | 'model3d'
  // contract-v14: Audio Studio
  | 'song'
  | 'music'
  | 'sfx'
  // contract-v16
  | 'speech'
export type GenerationStatus = 'queued' | 'generating' | 'ready' | 'approved' | 'rejected' | 'failed'

export interface Generation {
  id: string
  target_type: TargetType
  target_id: string
  kind: GenerationKind
  version: number
  status: GenerationStatus
  prompt: string
  params: Record<string, unknown>
  seed: number | null
  media_url?: string | null
  media_type?: string | null
  score?: number | Record<string, number> | null
  note?: string | null
  created_at: string
  approved_at?: string | null
  parent_id?: string | null
  job_id?: string | null
  // small webp preview for grids (P5); older servers leave it out
  thumb_url?: string | null
}

export interface Character {
  id: string
  // null for a Character Studio character (contract-v25); films cast it through project_cast
  project_id: string | null
  name: string
  description: string
  source: Source
  locked: boolean
  profile?: CharacterProfile
  voice_id?: string | null
  // how they move on camera (Character Looks)
  performance?: { expressiveness?: 'calm' | 'natural' | 'lively' | null; gestures?: string }
  studio?: boolean
  approved_portrait?: Generation | null
  // approved, else the newest finished portrait
  portrait?: Generation | null
  panels_ready?: number
  board_thumb_url?: string | null
  films?: { id: string; title: string }[]
  updated_at?: string
}

// contract-v25-character-sheets.md
export const PROFILE_FIELDS = [
  'role', 'affiliation', 'nationality', 'height', 'age', 'gender', 'languages', 'personality', 'accent', 'wardrobe',
] as const
export type ProfileField = (typeof PROFILE_FIELDS)[number]
export type CharacterProfile = Partial<Record<ProfileField, string>>

export type SheetPreset = 'essential' | 'full' | 'custom'
export type SheetBackdrop = 'grey' | 'dark'
export type BoardLayout = 'dossier' | 'studio'
export type SheetListName = 'outfits' | 'details' | 'props' | 'accessories'
export type SheetPanelStatus = 'none' | 'waiting' | 'queued' | 'generating' | 'ready' | 'approved' | 'failed' | 'rejected'

export interface SheetPanel {
  key: string
  section: string
  section_title: string
  label: string
  default_label: string
  source: 'portrait' | 'hero' | 'none'
  status: SheetPanelStatus
  current: Generation | null
  live_job_id: string | null
  versions: number
  stale: boolean
}

export interface CharacterSheet {
  character_id: string
  preset: SheetPreset | null
  counts: Record<string, number>
  lists: Partial<Record<SheetListName, string[]>>
  backdrop: SheetBackdrop
  titles: Record<string, string>
  footer: { team?: string; number?: string }
  ruler: boolean
  panels: SheetPanel[]
  palette: string[]
  estimate: { panels: number; seconds_low: number; seconds_high: number }
  has_portrait: boolean
  // uploaded: the user's own sheet is the board, and nothing redraws over it until they lay out a new one
  board: { media_id: string | null; layout: BoardLayout; generation: Generation | null; status: 'none' | 'queued' | 'running' | 'ready'; uploaded?: boolean }
  // the sheet the user uploaded, to cut panels from
  own_sheet?: { media_id: string; generation: Generation; width: number | null; height: number | null } | null
  queued?: number
  saved?: number
}

/** A crop of the uploaded sheet in its own pixels; panel "portrait" makes a portrait version. */
export interface SheetCrop {
  panel: string
  x: number
  y: number
  w: number
  h: number
}

export interface PhotoConsentRecord {
  name: string
  statement: string
}

export interface SheetCatalogue {
  sections: { key: string; label: string }[]
  panels: { key: string; section: string; label: string }[]
  counts: Record<string, number[]>
  lists: Record<SheetListName, number>
  presets: Record<'essential' | 'full', { counts: Record<string, number>; panels: number }>
  backdrops: SheetBackdrop[]
  seconds_per_panel: [number, number]
}

export interface SheetRequest {
  preset: SheetPreset
  counts?: Record<string, number>
  lists?: Partial<Record<SheetListName, string[]>>
  backdrop?: SheetBackdrop
}

export interface SheetTextPatch {
  titles?: Record<string, string>
  labels?: Record<string, string>
  footer?: { team?: string; number?: string }
  ruler?: boolean
  backdrop?: SheetBackdrop
  lists?: Partial<Record<SheetListName, string[]>>
}

export interface ShotRef {
  kind: 'character' | 'location'
  id: string
  name: string
  panel: string | null
  panel_label: string | null
  auto: boolean
  generation: Generation | null
  options: { key: string; label: string; section_title: string }[]
  used: boolean
}

// contract-v1-storyboard.md
export interface Location {
  id: string
  project_id: string
  name: string
  description: string
  source: Source
  locked: boolean
  time_of_day_variants: string[]
  approved_establishing?: Generation | null
  created_at: string
  updated_at: string
}

export type ShotType =
  | 'wide'
  | 'medium'
  | 'close_up'
  | 'extreme_close_up'
  | 'over_shoulder'
  | 'pov'
  | 'insert'
  | 'establishing'
  | 'long_take'
export type SeamMode = 'cut' | 'continue'
export type PromptMode = 'auto' | 'manual'
export type ShotStatus = 'draft' | 'frames_ready' | 'rendering' | 'take_ready' | 'approved'

export interface Shot {
  id: string
  scene_id: string
  project_id: string
  order: number
  shot_type: ShotType
  duration_s: number
  description: string
  camera: string
  prompt: string
  prompt_mode: PromptMode
  start_prompt?: string | null
  end_prompt?: string | null
  motion_prompt?: string | null
  character_ids: string[]
  location_id?: string | null
  seam_in: SeamMode
  handoff_text: string
  status: ShotStatus
  stale: boolean
  source: Source
  locked: boolean
  start_frame?: Generation | null
  end_frame?: Generation | null
  start_linked?: boolean
  approved_take?: Generation | null
  takes_count: number
  // contract-v2: per-chunk action prompts for long takes; older servers omit it
  beats?: Beat[]
  // contract-v7: where the brand kit's logo/products appear; older servers omit them
  brand_placements?: BrandPlacement[]
  brand_placements_locked?: boolean
  closing?: string | null
  brand_closing?: string | null
  // P3: generations in flight, and kinds whose newest try failed; older servers omit it
  activity?: ShotActivity[]
  // P2 camera rack on a shot (migration 0007); `camera` holds the same in plain words
  camera_rack?: CameraSetting
  // contract-v25: {character_id: sheet panel} picked by hand over the automatic reference
  ref_panels?: Record<string, string>
  // contract-v26: spoken in each character's voice and lip-synced; the notice names speakers without a voice
  dialogue?: DialogueLine[]
  dialogue_notice?: string | null

  created_at: string
  updated_at: string
}

// contract-v26-character-voices.md
export interface DialogueLine {
  character_id: string
  text: string
  pause_after_s?: number | null
}

export interface CharacterVoiceDesign {
  character: Character
  voice: Voice
  description: string
  understood: string[]
  ignored: string[]
}

export interface TestClip {
  item: MediaItem | null
  text: string
  panel?: string | null
  job?: Job | null
  speech_jobs?: Job[]
}

export interface ShotActivity {
  id: string
  kind: GenerationKind
  status: 'queued' | 'generating' | 'failed'
  job_id?: string | null
  version: number
}

export type ShotPatch = Partial<
  Pick<
    Shot,
    | 'shot_type'
    | 'duration_s'
    | 'description'
    | 'camera'
    | 'prompt'
    | 'prompt_mode'
    | 'start_prompt'
    | 'end_prompt'
    | 'motion_prompt'
    | 'character_ids'
    | 'location_id'
    | 'seam_in'
    | 'handoff_text'
    | 'beats'
    | 'brand_placements'
    | 'camera_rack'
    | 'ref_panels'
    | 'dialogue'
  >
>

export interface StoryboardRequest {
  mode: 'scene' | 'shots'
  scene_ids?: string[]
  generate_frames: boolean
  overwrite: boolean
  continuity?: 'chain'
  // P3: plan the shot list, hold the frames until it's approved
  review_first?: boolean
}

// UI polish P3 (contract-v8-polish.md)
export interface ShotExtendResult {
  shot: Shot
  job: Job
}

export interface ShotRerenderResult {
  jobs: Job[]
  linked_next_shot_id?: string | null
}

export interface ShotsApproveResult {
  scene: Scene
  jobs: Job[]
}

export interface QuickStageThumb {
  id: string
  kind: GenerationKind
  url: string | null
  video: boolean
}

export interface QuickStageMedia {
  outline?: { text?: string }
  cast?: QuickStageThumb[]
  storyboard?: QuickStageThumb[]
  render?: QuickStageThumb[]
  stitch?: QuickStageThumb[]
}

export type RegenerateMode = 'same' | 'note' | 'edit'

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

export interface Job {
  id: string
  type: string
  status: JobStatus
  progress: number
  message: string
  project_id?: string | null
  generation_id?: string | null
  gpu?: string | null
  // which GPU worker runs it when the server splits image and video work
  lane?: 'image' | 'video' | 'audio' | 'general'
  attempts: number
  error?: string | null
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  gpu_seconds?: number | null
  result?: (AiJobResult & Record<string, unknown>) | null
}

// AI writing (contract-v0 "AI" section)
export type AssistAction = 'draft_from_idea' | 'expand' | 'tighten' | 'rewrite_tone' | 'write_dialogue' | 'suggest_logline'
export type SuggestionStatus = 'pending' | 'accepted' | 'rejected'

export interface Suggestion {
  id: string
  project_id: string
  target_type: 'scene' | 'character'
  target_id: string
  field: string
  current_text: string
  proposed_text: string
  action: string
  status: SuggestionStatus
  job_id?: string | null
  created_at: string
  resolved_at?: string | null
}

export interface SuggestionResult {
  suggestion: Suggestion
  scene?: Scene | null
  character?: Character | null
}

export interface AiJobResult {
  thinking?: string
  prompt?: string
  outcome?: 'written' | 'suggested' | 'unchanged'
  scene_ids?: string[]
  drafted_ids?: string[]
  character_ids?: string[]
  suggestion_ids?: string[]
  calls?: { role: string; model: string; seconds: number; step?: string }[]
}

// contract-v2-longtake.md
export interface Beat {
  t_start: number
  t_end: number
  prompt: string
  source: Source
  locked: boolean
  // the server sets this when the duration changed under an unlocked beat
  stale?: boolean
}

export interface ShotEstimate {
  chunks: number
  frames_total: number
  est_gpu_s: number
  est_wall_s: number
  // not in the contract yet; read it when the server sends it
  max_s?: number
}

export type ChunkStatus = 'queued' | 'generating' | 'done' | 'failed'

export interface TakeChunk {
  idx: number
  t_start: number
  t_end: number
  frames: number
  status: ChunkStatus
  file?: string | null
  seed?: number | null
  gpu_seconds?: number | null
  progress?: number | null
}

// contract-v3-reel.md
export type TransitionKind = 'cut' | 'dissolve' | 'fade_black'
export type MezzanineStatus = 'fresh' | 'stale' | 'missing' | 'building'

export interface ReelClip {
  id: string
  shot_id: string
  scene_id: string
  order: number
  generation_id: string
  media_url: string
  thumb_url?: string | null
  source_duration_s: number
  trim_in_s: number
  trim_out_s: number
  duration_s: number
  transition_in: TransitionKind
  transition_s: number
  enabled: boolean
  changed: boolean
}

export type ReelClipPatch = Partial<Pick<ReelClip, 'trim_in_s' | 'trim_out_s' | 'transition_in' | 'transition_s' | 'enabled'>>

export interface ReelScene {
  scene_id: string
  heading: string
  order: number
  duration_s: number
  mezzanine: { status: MezzanineStatus; generation_id?: string | null }
  clips: ReelClip[]
}

export interface Reel {
  id: string
  project_id: string
  duration_s: number
  scenes: ReelScene[]
  missing: { shot_id: string; scene_id: string; reason: string }[]
  last_render?: Generation | null
}

export interface ReelEstimate {
  clips: number
  duration_s: number
  stale_scenes: number
  est_seconds: number
}

// Range stitching (contract-v3 "Range stitching & Output"). SSE generation events omit the
// top-level extras, so read them through renderInfo() which falls back to params.
export interface Render extends Generation {
  title?: string
  scene_range?: string
  scene_ids?: string[]
  full?: boolean
  duration_s?: number | null
  clips?: number | null
  approved?: boolean
}

export interface StitchRequest {
  quality: 'draft'
  scene_ids?: string[]
  title?: string
}

// contract-v4-upscale-quick.md
export type UpscaleEngineId = 'best' | 'fast' | 'quick'
export type UpscaleTarget = '1080p' | '1440p' | '4k'

export interface UpscaleEngine {
  id: UpscaleEngineId
  label: string
  available: boolean
  reason?: string | null
  scales: number[]
  est_gpu_s_per_output_s: number
}

export interface UpscaleOptions {
  engines: UpscaleEngine[]
  default_engine: UpscaleEngineId
  // the server sends {id, label, width, height}; older builds sent bare ids
  targets: (UpscaleTarget | { id: UpscaleTarget; label?: string })[]
}

export interface UpscaleRequest {
  engine: UpscaleEngineId
  target: UpscaleTarget
}

// image upscaling (contract v4, "Image upscaling")
export type ImageUpscaleEngineId = 'redraw' | 'quick' | 'anime' | 'best'
export type ImageUpscaleTarget = '2x' | '4x' | '2k' | '4k'

export interface ImageUpscaleEngine {
  id: ImageUpscaleEngineId
  label: string
  description?: string
  available: boolean
  reason?: string | null
  max_long_side: number
  multiple?: number
  max_mp?: number | null
  denoise?: { min: number; max: number; default: number }
  variants?: ('3b' | '7b')[]
  default_variant?: '3b' | '7b'
}

export type ImageUpscaleEstimate =
  | { allowed: true; width: number; height: number; capped?: boolean; est_gpu_s: number }
  | { allowed: false; reason: string }

export interface ImageUpscaleOptions {
  media: 'image'
  engines: ImageUpscaleEngine[]
  default_engine: ImageUpscaleEngineId
  targets: { id: ImageUpscaleTarget; label: string }[]
  source: { width: number; height: number; prompt?: string }
  estimates: Partial<Record<ImageUpscaleEngineId, Partial<Record<ImageUpscaleTarget, ImageUpscaleEstimate>>>>
}

export interface ImageUpscaleRequest {
  engine: ImageUpscaleEngineId
  target: ImageUpscaleTarget
  variant?: '3b' | '7b'
  denoise?: number
}

export type SegmentStatus = 'queued' | 'generating' | 'done' | 'failed'

export interface UpscaleSegment {
  idx: number
  t_start: number
  t_end: number
  status: SegmentStatus
  file?: string | null
}

export type QuickStyle = 'cinematic' | 'documentary' | 'animated' | 'commercial'
export type QuickAspect = '16:9' | '9:16' | '1:1'

export interface QuickRequest extends MagicFields {
  prompt: string
  duration_s: number
  aspect_ratio: QuickAspect
  style: QuickStyle
  dialogue: boolean
  upscale: UpscaleRequest | null
  // contract-v6, omitted for the server default
  image_model?: string
  video_quality?: 'standard' | 'hq'
  brand_kit_id?: string
}

export type QuickStageKey = 'outline' | 'cast' | 'storyboard' | 'render' | 'stitch' | 'upscale'
export type QuickStageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface QuickStage {
  key: QuickStageKey
  label: string
  status: QuickStageStatus
  started_at?: string | null
  finished_at?: string | null
  detail?: string | null
}

export interface QuickJobResult {
  stages?: QuickStage[]
  preview_ids?: string[]
  final_render_id?: string | null
  eta_s?: number | null
  // P3: [low, high] seconds left, and per-stage thumbnail strips; older servers omit both
  eta_range_s?: [number, number] | null
  stage_media?: QuickStageMedia
}

export interface QuickRecent {
  project: Project
  job: Job
  final_render?: Generation | null
}

// contract-v5-nav-image.md
// 'model' is a 3D Studio result, a GLB (contract v40)
export type MediaKind = 'image' | 'video' | 'audio' | 'model'
// what has pixels: upscaling, compare, looks
export type VisualKind = 'image' | 'video'
export type MediaOrigin = 'generated' | 'upload' | 'project'

export interface MediaItem {
  id: string
  workspace_id: string
  kind: MediaKind
  origin: MediaOrigin
  title: string
  tags: string[]
  project_id?: string | null
  generation_id: string
  width?: number | null
  height?: number | null
  duration_s?: number | null
  media_url?: string | null
  thumb_url?: string | null
  created_at: string
  updated_at: string
  versions_count: number
  // not in the contract; read when the server sends it, otherwise derived from the generation
  status?: GenerationStatus
  prompt?: string
  seed?: number | null
  // newer servers echo the generation params (model, model_resolved, brand_kit_id…) on the item
  params?: Record<string, unknown> | null
  model?: string | null
  // contract v8 P4; older servers leave them out
  folder_id?: string | null
  favourite?: boolean
  // P5: the current version is an upscale ("4K", "2×"…), and the version it all started from
  upscale?: { target: string; label: string; engine?: string | null } | null
  original_generation_id?: string | null
  // photo catalogue (contract v11); older servers leave them out
  rating?: number
  flag?: '' | 'pick' | 'reject'
  label?: '' | 'red' | 'yellow' | 'green' | 'blue' | 'purple'
  caption?: string
  captured_at?: string | null
  camera?: string | null
  lens?: string | null
  focal_mm?: number | null
  aperture?: number | null
  shutter_s?: number | null
  iso?: number | null
  original_name?: string | null
  bytes?: number | null
  source_type?: string | null
  edited?: boolean
  album_ids?: string[]
  duplicate?: boolean
  // contract v15: audio, once measured
  analysis?: AudioAnalysisBrief | null
  // contract v37: the studio this is a working part of; null is a final output
  home?: string | null
}

export interface MediaDetail extends MediaItem {
  versions: Generation[]
}

export interface MediaPage {
  items: MediaItem[]
  next_cursor?: string | null
}

export interface MediaQuery {
  kind?: MediaKind
  origin?: MediaOrigin
  q?: string
  tag?: string
  project_id?: string
  include?: 'project'
  folder_id?: string
  favourite?: 1
  // contract v34, admin only: just the items made through the API
  via?: 'api'
  // contract v37: only this studio's parts ('any' = every part), or a comma list of studios added on top
  home?: string
  include_home?: string
  limit?: number
  cursor?: string
}

// contract v8 P4: library organisation
export type FolderKind = 'any' | MediaKind

export interface Folder {
  id: string
  name: string
  parent_id: string | null
  kind: FolderKind
  sort: number
  item_count: number
  created_at: string
  updated_at: string
}

export interface SavedFilter {
  id: string
  name: string
  query: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type BatchAction = 'delete' | 'download' | 'upscale' | 'move' | 'tag'

export interface BatchResult {
  action: BatchAction
  done: string[]
  skipped: { ref: string; reason: string }[]
  jobs: Job[]
}

export type ImageAspect = '1:1' | '16:9' | '9:16' | '4:3' | '3:4' | '2:3' | '3:2'

// magic prompt (contract-v8 draft): omitted means "auto"
export type MagicMode = 'auto' | 'on' | 'off'

export interface MagicFields {
  magic_prompt?: MagicMode
  // the prompt is an enhancement the user already reviewed; don't enhance it again
  prompt_enhanced?: boolean
}

export interface PromptEnhanceRequest {
  prompt: string
  kind: 'image' | 'video' | 'quick' | 'edit'
  model?: string
  style?: string
  brand_kit_id?: string
  mode: MagicMode
}

export interface PromptEnhanceResult {
  enhanced: string
  changed: boolean
  notes?: string | null
}

export interface EstimateQuery {
  // model3d: model is the engine and speed the quality (draft | final)
  kind: 'image' | 'video' | 'take' | 'upscale' | 'audio' | 'model3d'
  model?: string
  speed?: string
  count?: number
  duration_s?: number
  width?: number
  height?: number
  // model3d: add the side and back views made first
  better_back?: boolean
}

export interface EstimateResult {
  low_s: number
  high_s: number
  basis: 'measured' | 'rough'
  samples?: number
  // a model load, already included in both ends
  load_s?: number
}

export interface ImageGenerateRequest extends MagicFields {
  // P4 "Generate into…"
  folder_id?: string
  prompt: string
  negative?: string
  aspect: ImageAspect
  count: number
  style?: string
  seed?: number
  steps?: number
  template_id?: string
  model?: string
  speed?: string
  brand_kit_id?: string
}

export interface ImageEditRequest extends MagicFields {
  // P4 "Generate into…"
  folder_id?: string
  source_ids: string[]
  instruction: string
  aspect?: ImageAspect
  count?: number
  // separate pictures, 2-25, each its own version of the instruction; one job makes the set
  set_size?: number
  seed?: number
  model?: string
  brand_kit_id?: string
  // contract v30: a template's image tool (LaMa, IC-Light, CatVTON, line art...)
  template_id?: string
  tool?: string
  engine?: 'auto' | 'tool' | 'edit'
  tool_options?: { light?: LightFrom; garment_type?: 'upper' | 'lower' | 'overall'; edge?: 'crisp' | 'soft' }
  // LaMa: PNG data URL, opaque = remove
  mask?: string
}

export type LightFrom = 'none' | 'left' | 'right' | 'top' | 'bottom'

/** GET /images/tools (contract v30). Tools switched off on the server aren't listed. */
export interface ImageToolInfo {
  id: string
  label: string
  engine: string
  licence: string
  noncommercial: boolean
  fallback: boolean
  needs_mask: boolean
  available: boolean
  reason: string | null
  est_seconds?: number
}

/** What a template card says about the tool behind it. */
export type TemplateTool = Pick<ImageToolInfo, 'id' | 'label' | 'engine' | 'licence' | 'noncommercial' | 'fallback' | 'needs_mask'>

/** GET /images/options styles (contract v31): prompt modifiers, grouped, with a 3:4 example picture when rendered. */
export interface ImageStyle {
  id: string
  group: string
  label: string
  subtitle: string
  suffix: string
  negative: string
  best_model: string
  preview_url: string | null
}

export interface StyleGroup {
  id: string
  label: string
  // Image to Image starts its slider here for this group
  restyle_strength?: number
}

export interface ImageOptions {
  styles: ImageStyle[]
  style_groups: StyleGroup[]
  style_aliases: Record<string, string>
}

export type ControlType = 'depth' | 'canny' | 'pose' | 'hed' | 'scribble'

/** GET /images/tools "control" (contract v31): the Z-Image ControlNet and the map types this server can make. */
export interface ControlStatus {
  engine: string
  licence: string
  available: boolean
  reason: string | null
  fallback: string
  strength: { min: number; max: number; default: number }
  types: { id: ControlType; label: string; detector: string; licence: string; noncommercial: boolean; available: boolean; reason: string | null }[]
}

export interface ImageControlRequest {
  folder_id?: string
  source_id: string
  control: ControlType
  use_as_is?: boolean
  prompt: string
  style?: string
  strength: number
  aspect: Img2ImgAspect
  count: number
  seed?: number
  negative?: string
  engine?: 'auto' | 'controlnet' | 'edit'
  template_id?: string
}

/** POST /my-templates: Save as template (contract v31). */
export interface UserTemplateRequest {
  title: string
  description?: string
  target: 'generate' | 'edit'
  prompt: string
  negative?: string
  style?: string | null
  model?: string | null
  aspect?: ImageAspect | null
  count: number
  ref_labels?: string[]
  examples?: Record<string, string>
  from_media_id?: string
}

export type Img2ImgAspect = 'source' | ImageAspect

export interface Img2ImgRequest extends MagicFields {
  // P4 "Generate into…"
  folder_id?: string
  source_id: string
  // may be empty when a style is set (contract v31)
  prompt: string
  style?: string
  template_id?: string
  strength: number
  count: number
  aspect: Img2ImgAspect
  model?: string
  speed?: string
  seed?: number
  brand_kit_id?: string
}

export interface MediaBatch {
  items: MediaItem[]
  jobs: Job[]
  // contract v14: what "auto" turned into
  model_resolved?: string | null
  // contract v30: the image tool that ran, or why the edit model ran instead
  tool_resolved?: string | null
  tool_fallback?: string | null
  // contract v35: the kit, logo variant and surface a brand card used
  brand_template?: { kit_id: string; kit_name: string; logo_variant: string; surface: BrandSurface; mode: 'photo' | 'scene' } | null
}

export type TemplateType = 'video' | 'image' | 'audio'

export interface Template {
  id: string
  type: TemplateType
  title: string
  description: string
  thumb?: string | null
  defaults: Record<string, unknown>
  // prompt templates (v2 catalogue); older servers leave these out
  category?: string | null
  tags?: string[]
  examples?: Record<string, string>
  preview_url?: string | null
  requires_brand?: boolean
  // v29: image templates that open Edit Image, with the pictures they need
  target?: 'image-generate' | 'image-edit' | 'image-control' | 'image-brand' | null
  refs_required?: number
  ref_labels?: string[]
  options?: TemplateOption[]
  // v30: the image tool behind an edit card
  tool?: TemplateTool | null
  // v31: Depth / Sketch / Pose to image cards
  control?: { type: ControlType; label: string; detector: string; licence: string; noncommercial: boolean; as_is: boolean } | null
  // v31: saved in this workspace; can be renamed and deleted
  mine?: boolean
  // v35 Brand templates: kit assets the card fills in, the usual surface, and the scene it can make
  brand_refs?: string[]
  surface?: BrandSurface | null
  scene?: { prompt_scaffold: string; aspect?: string; model?: string } | null
}

export type BrandSurface = 'light' | 'dark'

/** POST /images/brand (contract v35): {tokens} are filled from the kit on the server. */
export interface BrandImageRequest {
  template_id: string
  mode: 'photo' | 'scene'
  source_id?: string
  brand_kit_id?: string
  product_id?: string
  surface?: BrandSurface
  instruction?: string
  scene_prompt?: string
  aspect?: ImageAspect
  count?: number
  folder_id?: string
}

/** A picker that fills one [slot] of a template's prompt or instruction. */
export interface TemplateOption {
  slot: string
  label: string
  // v35: brand cards say whether a choice is a dark surface (the logo then goes light)
  choices: { label: string; value: string; aspect?: ImageAspect; style_id?: string; surface?: BrandSurface }[]
  // v31: "styles" shows the style gallery instead of a plain list
  gallery?: 'styles'
}

export interface TemplateStart {
  target: 'quick' | 'studio' | 'image' | 'image-edit' | 'image-control' | 'image-brand' | 'audio'
  prefill: Record<string, unknown>
}

export interface Dashboard {
  recent_projects: Project[]
  recent_videos: MediaItem[]
  recent_images: MediaItem[]
  running_jobs: Job[]
  quick_recent: QuickRecent[]
}

export interface TemplateCheck {
  ok: boolean
  missing_nodes: string[]
  missing_models: string[]
  invalid: string[]
  warnings: string[]
}

export interface ComfyCheck {
  ok: boolean
  url: string
  error?: string
  templates: Record<string, TemplateCheck>
}

export interface LlmRole {
  model: string | null
  present: boolean
  latency_ms?: number
  error?: string
}

export interface LlmCheck {
  ok: boolean
  url: string
  reasoning_format?: string
  roles: Partial<Record<'reasoning' | 'creative' | 'vision', LlmRole>>
  error?: string
}

// contract-v6-models.md
export type ModelType = 'image' | 'edit' | 'video' | 'upscale' | 'audio' | 'model3d'
export type ModelBadgeId = 'FAST' | 'BEST' | 'TEXT' | 'NEW' | 'HQ' | 'QUICK' | 'UPSCALE' | 'ANIME' | 'PHOTO' | '2K'
// contract v32: picker sections for image models, in this order
export type ModelGroup = 'fast' | 'photo' | 'text' | 'anime' | 'experimental'
export type LicenceKind = 'commercial' | 'conditional' | 'noncommercial'
export type ModelCapability =
  | 't2i' | 'edit' | 'refs' | 't2v' | 'i2v' | 'flf' | 'audio' | 'longtake' | 'text_render' | 'multi_angle' | 'i2i'
  // contract v14 (audio models)
  | 'song' | 'music' | 'sfx' | 'lyrics' | 'vocals' | 'instrumental' | 'timbre_ref' | 'long'
  // contract v40
  | 'image_to_3d' | 'multiview'

export interface ModelSpeed {
  id: string
  label: string
  steps: number
  note?: string | null
  available?: boolean
  reason?: string | null
}

export interface ModelInfo {
  id: string
  type: ModelType
  label: string
  badge: ModelBadgeId | null
  description: string
  capabilities: ModelCapability[]
  speeds?: ModelSpeed[]
  default_speed?: string
  max_refs?: number
  max_duration_s?: number
  est_seconds?: number
  estimate_source?: 'measured' | 'rough'
  available: boolean
  reason?: string | null
  default: boolean
  // LTX only: the temporal x2 upscaler, single-pass clips only
  smooth_motion?: { available: boolean; reason?: string | null }
  // audio models: the licence line, and MiniMax's non-commercial flag; image/edit models too (contract v32)
  extra?: { licence?: string; noncommercial?: boolean; licence_kind?: LicenceKind } & Record<string, unknown>
  // contract v32 (image / edit): false when ComfyUI lacks the nodes or weights, null when it couldn't be asked
  installed?: boolean | null
  group?: ModelGroup
  best_for?: string[]
}

export type VideoAspect = '16:9' | '9:16' | '1:1'

export interface VideoGenerateRequest extends MagicFields {
  // P4 "Generate into…"
  folder_id?: string
  prompt: string
  model: string
  duration_s: number
  // Image to Video leaves it out so the clip keeps the picture's shape
  aspect?: VideoAspect
  image_id?: string
  end_image_id?: string
  seed?: number
  smooth_motion?: boolean
  brand_kit_id?: string
  // contract v8 P2: ids from lib/camera.ts
  camera?: { size?: string; angle?: string; motion?: string; speed?: string }
}

// contract-v14: Audio Studio
export type AudioKind = 'song' | 'music' | 'sfx'
// boy / girl / choir / voice: sung by the model, then changed into that voice on the server
export type AudioVocal = 'male' | 'female' | 'duet' | 'none' | 'boy' | 'girl' | 'choir' | 'voice' | 'lead_group'
export type AudioCategory = 'music' | 'instrument' | 'loop' | 'sfx' | 'oneshot'

export interface AudioGenerateRequest {
  folder_id?: string
  kind: AudioKind
  prompt: string
  lyrics?: string
  language?: string
  vocal?: AudioVocal
  // vocal 'voice': the Library voice (or gallery:<id>) that sings it
  sing_voice_id?: string
  // vocal 'lead_group': who leads, and how many children sing with them
  lead_voice?: 'boy' | 'girl' | 'voice'
  group_size?: 3 | 5
  bpm?: number
  category?: AudioCategory
  duration_s: number
  model?: string
  count?: number
  seed?: number
  steps?: number
  timbre_ref_id?: string
  // Arabic-script songs (Urdu, Arabic, Persian...): the server adds the vowel marks to unmarked lines before singing
  aerab?: boolean
  // ACE-Step only
  keyscale?: string
  time_signature?: '2' | '3' | '4' | '6'
  guidance?: number
  think?: boolean
  title?: string
}

export interface LyricsRequest {
  topic: string
  language: string
  mood?: string
  style?: string
  sections?: string[]
  aerab?: boolean
}

export interface LyricsResponse {
  lyrics: string
  tags?: string | string[] | null
  marked?: number
  kept?: number
  low_coverage?: number
}

export interface AerabResponse {
  lyrics: string
  marked: number
  // lines left as written: the marking pass changed their letters
  kept: number
  low_coverage?: number
  language?: string | null
}

// contract-v23: POST /text/aerab, the one "Add aerab" every editor calls
export interface TextAerabRequest {
  text: string
  // empty: the server reads the language from the letters
  language?: string
}

export interface TextAerabResponse {
  text: string
  marked: number
  kept: number
  // lines still sparsely marked after the strict retry
  low_coverage: number
  language: string | null
}

// quality and smooth motion come from project.settings; the server drops them for long takes
export interface TakesRequest {
  count?: number
  duration_s?: number
}

/** GET /api/upscales (P5): one upscale result with the version it was made from. */
export interface UpscaleRow {
  result: Generation
  source?: Generation | null
  title: string
  kind: VisualKind
  media_id?: string | null
  project_id?: string | null
  engine?: string | null
  target?: string | null
  label?: string | null
  width?: number | null
  height?: number | null
  status: GenerationStatus
  job?: Job | null
  error?: string | null
}

export interface UpscalePage {
  items: UpscaleRow[]
  next_cursor?: string | null
}

// contract-v15: Audio editor, analysis and Score a Clip
export type FadeCurve = 'tri' | 'qsin' | 'exp' | 'log'
export type ChainPreset = 'broadcast' | 'cinematic' | 'podcast' | 'warm' | 'raw' | 'radio' | 'telephone' | 'hall'
export type LoudnessTarget = 'web' | 'podcast' | 'broadcast' | 'acx'
export type AudioFlag = 'clipping' | 'too_loud' | 'too_quiet' | 'long_silence'

export interface TimeRegion {
  start: number
  end: number
}

export interface AudioMarker {
  t: number
  label: string
}

export interface AudioEdit {
  version?: 1
  trim?: TimeRegion | null
  cuts?: TimeRegion[]
  gain_db?: number
  fade_in?: { s: number; curve: FadeCurve } | null
  fade_out?: { s: number; curve: FadeCurve } | null
  speed?: number
  pitch?: number
  silence_trim?: boolean
  loop_to?: number | null
  append?: string[]
  chain?: { preset: ChainPreset; amount: number } | null
  loudness?: { target: LoudnessTarget } | null
  markers?: AudioMarker[]
}

export interface Peaks {
  duration_s: number
  sample_rate: number
  channels: number
  peaks: [number, number][]
}

export interface AudioAnalysis {
  lufs_i: number | null
  lra: number | null
  true_peak_db: number | null
  sample_peak_db?: number | null
  clipped_pct?: number | null
  silences: TimeRegion[]
  flags: AudioFlag[]
}

// what list items carry once the server has measured them
export type AudioAnalysisBrief = Pick<AudioAnalysis, 'lufs_i' | 'true_peak_db' | 'flags'>

export interface AudioPreview {
  url: string
  durationS: number | null
  lufsI: number | null
  truePeakDb: number | null
  renderMs: number | null
}

export type AudioExportFormat = 'wav16' | 'wav24' | 'wav32f' | 'flac' | 'mp3_128' | 'mp3_192' | 'mp3_320' | 'opus' | 'mp4'

export interface MixTrack {
  media_id: string
  offset_s: number
  gain_db: number
  mute: boolean
  fade_in_s: number
  fade_out_s: number
  loop: boolean
  duck: boolean
}

export interface MixIn {
  video_id: string
  original: { keep: boolean; gain_db: number }
  tracks: MixTrack[]
  duck: { amount_db: number; under: 'original' }
  loudness?: { target: LoudnessTarget } | null
}

// contract-v16: Voice studio
export type VoiceKind = 'clone' | 'design' | 'preset'
export type VoiceGender = 'male' | 'female' | 'neutral'
export type VoiceFeature = 'clone' | 'design' | 'tags' | 'emotion' | 'preset_voices' | 'fast'

export interface VoiceLanguage {
  code: string
  name: string
}

export interface VoiceEngine {
  id: string
  label: string
  features: VoiceFeature[]
  languages: VoiceLanguage[]
  licence: string
  default_for?: string[]
  presets?: { id: string; name: string; language: string }[]
}

export interface VoiceRef {
  media_id: string
  generation_id: string
  duration_s: number | null
  url: string | null
}

export interface VoiceDesign {
  description: string
  seed?: number | null
  engine?: string | null
}

export interface VoiceConsent {
  name: string
  statement: string
  at?: string
  user_id?: string
}

export type RefWarning = 'short' | 'long'

export interface Voice {
  id: string
  name: string
  language: string
  notes: string
  gender_hint: VoiceGender | null
  kind: VoiceKind
  ref: VoiceRef | null
  ref_text: string
  design: VoiceDesign | null
  preset: { engine: string; id: string } | null
  portrait_url: string | null
  starred: boolean
  locked: boolean
  consent: VoiceConsent | null
  created_at: string
  updated_at: string
  uses: number
  // POST and PATCH only: the reference is usable but outside 5–15 s
  warnings?: RefWarning[]
}

export interface VoiceQuery {
  q?: string
  language?: string
  kind?: VoiceKind
  starred?: boolean
}

export interface VoiceCreate {
  name: string
  language: string
  kind: VoiceKind
  ref_media_id?: string
  ref_text?: string
  design?: VoiceDesign
  preset?: { engine: string; id: string }
  notes?: string
  gender_hint?: VoiceGender | null
  consent?: Pick<VoiceConsent, 'name' | 'statement'>
}

export type VoicePatch = Partial<Pick<Voice, 'name' | 'language' | 'notes' | 'gender_hint' | 'ref_text' | 'starred' | 'locked'>> & {
  ref_media_id?: string
  design?: VoiceDesign
}

export interface SpeakRequest {
  text: string
  voice_id?: string
  design?: { description: string; seed?: number }
  engine: string
  language: string
  aerab?: boolean
  speed?: number
  duration_s?: number
  steps?: number
  guidance?: number
  denoise?: boolean
  exaggeration?: number
  count?: number
  seed?: number
  title?: string
  folder_id?: string
  part_of?: string
}

export interface SpeakResponse extends MediaBatch {
  engine_resolved?: string | null
  // design previews on OmniVoice: what of the description it used, and what it had to drop
  understood?: string[] | null
  ignored?: string[] | null
}

export interface DesignPreviewRequest {
  description: string
  seed?: number
  engine?: string
  language: string
  text?: string
}

export interface DesignPreviewResponse extends SpeakResponse {
  seed: number
}

// contract-v24-video-podcast.md (the AI video podcast)
export type VPStageKey = 'cast' | 'set' | 'keyframes' | 'plan' | 'render' | 'assemble'
export type VPStageStatus = 'pending' | 'running' | 'review' | 'done' | 'failed' | 'skipped'
export type VPStatus = 'idle' | 'running' | 'review' | 'failed'
export type VPAspect = '16:9' | '9:16' | '1:1'
export type VPSubtitles = 'none' | 'soft' | 'burn'
export type VPSetPreset = 'modern_studio' | 'cosy_room' | 'news_desk' | 'rooftop' | 'desi_baithak'
export type VPQuality = 'draft' | 'final'
export type VPHostSource = 'persona' | 'photo' | 'existing' | 'studio'

export interface VPStage {
  key: VPStageKey
  label: string
  status: VPStageStatus
  detail: string | null
  started_at: string | null
  finished_at: string | null
}

export interface VPSettings {
  aspect: VPAspect
  style: string
  subtitles: VPSubtitles
  // burned subtitles: classic or animated
  caption_style?: CaptionStyle
  set_preset: VPSetPreset
  set_prompt: string
  cover_intro: boolean
  cover_outro: boolean
  cover_media_id: string | null
}

export interface VPConsent {
  name: string
  statement: string
  at?: string | null
  user_id?: string | null
}

export interface VPHost {
  host_id: string
  name: string
  role: string
  persona: string
  source: VPHostSource
  character_id: string | null
  photo_media_id: string | null
  photo_as_is?: boolean
  photo_url: string | null
  from_character_id: string | null
  // studio hosts: the Character Studio look they wear
  look_id?: string | null
  consent: VPConsent | null
  portrait: Generation | null
  sheet_views: { front?: Generation; three_quarter?: Generation }
}

export interface VPSet {
  location_id: string | null
  preset: VPSetPreset
  prompt: string
  description: string
  establishing: Generation | null
}

export interface VPAngle {
  key: string
  label: string
  host_id: string | null
  shot_id: string | null
  frame: Generation | null
}

export interface VPSegment {
  id: string
  index: number
  chapter_id: string | null
  title: string
  start: number
  end: number
  duration: number
}

// current / failed are 1-based piece numbers
export interface VPProgress {
  pieces: number
  done: number
  current: number | null
  failed: number | null
  status: string
}

export interface VPTrack {
  id: string
  kind: 'host' | 'wide'
  // a host id, or "wide"
  camera: string
  host_id: string | null
  segment_id: string
  angle_key: string
  start: number
  end: number
  duration: number
  pieces: number
  // the host's line ids in this segment
  lines: string[]
  // a cut shows it (host cameras always roll)
  used: boolean
  thumb_url: string | null
  takes: { draft: Generation | null; final: Generation | null }
  progress: { draft: VPProgress | null; final: VPProgress | null }
  needs: { draft: boolean; final: boolean }
}

export interface VPCut {
  id: string
  // a host id, "wide" or "cover"
  camera: string
  start: number
  end: number
  duration: number
  speaker_id: string | null
  text: string
  segment_id: string | null
}

export interface VPCamera {
  key: string
  label: string
  kind: 'host' | 'wide' | 'cover'
  host_id: string | null
  thumb_url: string | null
}

export interface VPPlan {
  segments: VPSegment[]
  tracks: VPTrack[]
  cuts: VPCut[]
  cameras: VPCamera[]
  // the voice gaps a cut snaps to
  snaps: number[]
}

export interface VPPlanStats {
  segments: number
  tracks: number
  host_tracks: number
  wide_tracks: number
  cuts: number
  pieces: number
  seconds: number
}

export interface VPOutput {
  media_id: string
  generation_id: string
  url: string
  quality: VPQuality
  duration_s: number
  built_at: string
  subtitles: VPSubtitles
  renderer: string
  fonts_missing: string[]
  fallback_shots: string[]
  width: number
  height: number
}

export interface VideoPodcast {
  id: string
  episode_id: string
  project_id: string
  title: string
  language: string
  stage: VPStageKey | 'done'
  status: VPStatus
  review: 'hosts' | 'look' | null
  stages: VPStage[]
  settings: VPSettings
  hosts: VPHost[]
  set: VPSet
  angles: VPAngle[]
  plan: VPPlan
  plan_stats: VPPlanStats
  duration_s: number | null
  mix_stale: boolean
  outputs: { draft: VPOutput | null; final: VPOutput | null }
  job: Job | null
  eta_s: number | null
  created_at: string
  updated_at: string
}

export type VPHostChoice =
  | { source: 'persona' }
  | { source: 'photo'; media_id: string; consent: { name: string; statement: string }; as_is?: boolean }
  | { source: 'existing'; character_id: string }
  | { source: 'studio'; character_id: string; look_id?: string | null }

export interface VPCutEdit {
  id?: string | null
  camera: string
  start: number
  end: number
}

export interface VideoPodcastPatch {
  hosts?: Record<string, VPHostChoice>
  settings?: Partial<VPSettings>
  cuts?: VPCutEdit[]
}

export interface VPRegenerate {
  what: 'portrait' | 'set' | 'angle'
  key?: string
  note?: string
}

export interface VPCharacterOption {
  id: string
  name: string
  description: string
  project_id: string
  project_title: string
  portrait_url: string | null
}

export interface VPEstimate {
  quality: VPQuality
  tracks: number
  takes: number
  pieces: number
  // footage the render makes; generated_s counts the overlaps too
  seconds: number
  generated_s: number
  low_s: number
  high_s: number
  basis: string
  label: string
}

// contract-v40-3d-studio.md
export type Engine3D = 'pixal3d' | 'trellis2'
export type Quality3D = 'draft' | 'final'

export type View3D = 'front' | 'left' | 'back' | 'right'
export type Start3D = 'picture' | 'text' | 'character' | 'views' | 'logo'

// a logo badge (contract v40 §8): extruded on the CPU, the art on the front
export interface Logo3D {
  depth: 'thin' | 'medium' | 'thick'
  bevel: 'none' | 'soft' | 'round'
  finish: 'gold' | 'chrome' | 'steel' | 'colour'
  back: 'mirror' | 'plain'
}

/** Exactly one of source_id, prompt, character_id, views or logo_id. A logo has no engine or quality. */
export interface Make3DRequest extends MagicFields {
  source_id?: string
  prompt?: string
  character_id?: string
  views?: Partial<Record<View3D, string>> & { front: string }
  logo_id?: string
  logo?: Logo3D
  // side and back views made first, for people and figures (Pixal3D only)
  better_back?: boolean
  engine?: Engine3D
  quality?: Quality3D
  remove_background: boolean
  brand_kit_id?: string
  image_model?: string
  seed?: number
  title?: string
  part_of?: string
}

export interface Make3DResponse {
  item: MediaItem
  job: Job
  model_resolved: string
  start?: Start3D
  background_note?: string | null
  // the start forced Pixal3D (several views) over the engine that was asked for
  engine_note?: string | null
  // a text start: the picture made first (a working part, home "3d")
  picture?: MediaItem | null
  picture_job?: Job | null
  views_used?: View3D[] | null
  better_back?: boolean
  better_back_note?: string | null
  back_views?: MediaItem[] | null
  back_jobs?: Job[] | null
  // a logo without transparency is cut out first
  cutout?: MediaItem | null
  cutout_job?: Job | null
}

// POST /3d/{id}/turntable: the new video item and the model with its new thumb_url
export interface Turntable3DResult {
  item: MediaItem
  model: MediaItem
  // false when the server kept the WebM (its ffmpeg couldn't make the MP4)
  transcoded: boolean
}

// POST /3d/{id}/shots: Ad Studio working parts (home "ad"), in the order sent
export interface Shots3DResult {
  items: MediaItem[]
  ids: string[]
}

// contract v42: rights and moderation
export interface RightsOverview {
  features: { key: FeatureKey; label: string; released: boolean; own_switch: boolean }[]
  models: { key: string; label: string; type: ModelType; released: boolean }[]
  packages: { id: string; name: string; kind: PackageKind; custom: boolean; features: Partial<Record<FeatureKey, boolean>>; models: string[] | null }[]
}

export interface UserRights {
  user_id: string
  admin: boolean
  package: string
  overrides: { features: Partial<Record<FeatureKey, boolean>>; models: Record<string, boolean> }
  features: Record<FeatureKey, boolean>
  models: string[] | null
  staff_role: 'manager' | null
  manager_scope: 'site' | 'team'
  team: string[]
}

export interface Notice {
  id: string
  kind: 'warning' | 'removal' | 'unblock'
  reason: string
  item_id: string | null
  at: string
}

export type ReviewState = 'queue' | 'reviewed' | 'removed' | 'all'

export interface ReviewItem {
  id: string
  kind: 'image' | 'video' | 'audio'
  origin: string
  title: string
  prompt: string
  media_type: string | null
  width: number | null
  height: number | null
  duration_s: number | null
  created_at: string
  owner: { id: string; email: string; name: string }
  reviewed_at: string | null
  removed_at: string | null
  removed_reason: string
  thumb_url: string | null
  file_url: string | null
}

export interface ModPerson {
  id: string
  email: string
  name: string
  is_active: boolean
  warnings: number
  blocked_at: string | null
  blocked_reason: string
}

export interface ModLogRow {
  id: string
  at: string
  actor: string
  action: string
  target_type: string
  target_id: string | null
  detail: Record<string, unknown>
}
