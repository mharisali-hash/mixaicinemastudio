import type { GenerationKind, MediaQuery, ModelType, TargetType, TemplateType, VisualKind, VoiceQuery } from '@/lib/types'

export const qk = {
  me: ['me'] as const,
  tokens: ['api-tokens'] as const,
  authConfig: ['auth-config'] as const,
  sitePlans: ['site-plans'] as const,
  adminUsers: (q: string, status: string, packageId = '') => ['admin-users', q, status, packageId] as const,
  // contract v39
  usage: ['account-usage'] as const,
  adminUser: (id: string) => ['admin-users', 'detail', id] as const,
  adminOverview: ['admin-overview'] as const,
  adminPackages: (archived: boolean) => ['admin-packages', archived] as const,
  adminSubscriptions: (status: string, packageId: string) => ['admin-subscriptions', status, packageId] as const,
  adminPayments: (status: string, gateway: string) => ['admin-payments', status, gateway] as const,
  adminPayment: (id: string) => ['admin-payments', 'detail', id] as const,
  adminUsage: (days: number, userId: string) => ['admin-usage', days, userId] as const,
  adminAudit: (action: string, q: string) => ['admin-audit', action, q] as const,
  // contract v42
  adminRights: ['admin-rights'] as const,
  userRights: (id: string) => ['admin-users', 'rights', id] as const,
  notices: ['account-notices'] as const,
  review: (filters: object) => ['moderation', 'items', filters] as const,
  modPeople: (q: string, status: string) => ['moderation', 'people', q, status] as const,
  modLog: ['moderation', 'log'] as const,
  system: ['system-status'] as const,
  projects: ['projects'] as const,
  project: (id: string) => ['projects', id] as const,
  scenes: (projectId: string) => ['scenes', projectId] as const,
  characters: (projectId: string) => ['characters', projectId] as const,
  // contract-v25; every list of characters stays under 'characters' (useGenerations patches them as arrays)
  studioCharacters: (q: string) => ['characters', 'studio', q] as const,
  character: (id: string) => ['character', id] as const,
  characterSheet: (id: string) => ['character-sheet', id] as const,
  sheetCatalogue: ['character-sheet-catalogue'] as const,
  testClip: (id: string) => ['character-test-clip', id] as const,
  shotRefs: (shotId: string) => ['shot-refs', shotId] as const,
  locations: (projectId: string) => ['locations', projectId] as const,
  // one project-wide list; scenes filter it client-side so SSE patches land in one place
  shots: (projectId: string) => ['shots', projectId] as const,
  generationsFor: (t: TargetType, id: string, kind: GenerationKind) => ['generations', t, id, kind] as const,
  generations: (t: TargetType, id: string, kind: GenerationKind, includeRejected: boolean) =>
    ['generations', t, id, kind, includeRejected] as const,
  jobs: ['jobs'] as const,
  // under qk.jobs, so anything that invalidates the queue refreshes this view too
  jobsVia: (via: 'api') => ['jobs', 'via', via] as const,
  shotEstimate: (shotId: string, durationS: number) => ['shot-estimate', shotId, durationS] as const,
  reel: (projectId: string) => ['reel', projectId] as const,
  reelEstimate: (projectId: string) => ['reel-estimate', projectId] as const,
  renders: (projectId: string) => ['renders', projectId] as const,
  suggestions: (projectId: string) => ['suggestions', projectId] as const,
  // single generations seen over SSE, so quick-create thumbnails can resolve preview_ids
  generation: (id: string) => ['generation', id] as const,
  upscaleOptions: ['upscale-options'] as const,
  quickRecent: ['quick-recent'] as const,
  quickJob: (projectId: string) => ['quick-job', projectId] as const,
  // contract-v5
  mediaLists: ['media', 'list'] as const,
  mediaList: (q: Omit<MediaQuery, 'cursor'>) => ['media', 'list', q] as const,
  mediaItem: (id: string) => ['media', 'item', id] as const,
  templates: (type: TemplateType) => ['templates', type] as const,
  dashboard: ['dashboard'] as const,
  comfyCheck: ['comfy-check'] as const,
  llmCheck: (ping: boolean) => ['llm-check', ping] as const,
  models: (type: ModelType) => ['models', type] as const,
  // contract-v7
  brandKits: ['brand-kits'] as const,
  brandKit: (id: string) => ['brand-kits', 'kit', id] as const,
  brandPreview: (id: string, kind: string, settings: string) => ['brand-preview', id, kind, settings] as const,
  // contract-v8 P4
  folders: ['folders'] as const,
  savedFilters: ['saved-filters'] as const,
  // P5
  upscalesAll: ['upscales'] as const,
  upscales: (kind: VisualKind) => ['upscales', kind] as const,
  // contract-v15
  peaks: (id: string, version: string | null, n: number) => ['audio-peaks', id, version, n] as const,
  audioAnalysis: (id: string, version: string | null) => ['audio-analysis', id, version] as const,
  // contract-v16
  voiceLists: ['voices', 'list'] as const,
  voices: (q: VoiceQuery) => ['voices', 'list', q] as const,
  voiceEngines: ['voice-engines'] as const,
  // contract-v19
  galleryAll: ['voice-gallery'] as const,
  gallery: (q: Record<string, string | undefined>) => ['voice-gallery', q] as const,
  // contract-v17
  scripts: ['scripts', 'list'] as const,
  script: (id: string) => ['scripts', 'one', id] as const,
  // contract-v18
  dubs: ['dubs', 'list'] as const,
  dub: (id: string) => ['dubs', 'one', id] as const,
  transcripts: ['transcripts', 'list'] as const,
  transcript: (id: string) => ['transcripts', 'one', id] as const,
  // contract-v20
  podcastShows: ['podcast', 'shows'] as const,
  podcastEpisodes: ['podcast', 'episodes'] as const,
  podcastEpisode: (id: string) => ['podcast', 'episode', id] as const,
  // contract-v21
  stems: (id: string) => ['audio-stems', id] as const,
  // contract-v43
  remixRecipes: ['audio-remix', 'recipes'] as const,
  remixAnalysis: (id: string) => ['audio-remix', 'analysis', id] as const,
  remixes: (id: string) => ['audio-remix', 'of', id] as const,
  // contract-v24; the estimate and characters sit under aiVideo(id) so one invalidate refreshes them
  episodeAiVideo: (episodeId: string) => ['podcast', 'episode-ai-video', episodeId] as const,
  aiVideo: (id: string) => ['podcast', 'ai-video', id] as const,
  aiVideoCharacters: (id: string) => ['podcast', 'ai-video', id, 'characters'] as const,
  aiVideoEstimate: (id: string, quality: string, tracks: string) => ['podcast', 'ai-video', id, 'estimate', quality, tracks] as const,
}
