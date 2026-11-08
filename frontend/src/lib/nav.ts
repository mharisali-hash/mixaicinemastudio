import {
  AudioWaveform,
  Camera,
  Captions,
  Languages,
  Aperture,
  Box,
  Boxes,
  Brush,
  Clapperboard,
  Film,
  Feather,
  Presentation,
  FolderUp,
  Home,
  ImageIcon,
  ImageUpscale,
  Images,
  LayoutTemplate,
  ListOrdered,
  Library,
  LibraryBig,
  Mic,
  MessagesSquare,
  Music,
  Music4,
  MapPin,
  Megaphone,
  Podcast,
  Scissors,
  Maximize2,
  Settings,
  ShieldUser,
  Printer,
  PenLine,
  Shuffle,
  Sparkles,
  SquareSplitHorizontal,
  Stamp,
  Stethoscope,
  SwatchBook,
  UserRound,
  Users,
  UsersRound,
  Video,
  Volume2,
  Wand2,
  Layers,
  type LucideIcon,
} from 'lucide-react'

import { FALLBACK_MODELS } from './models'
import type { FeatureKey, ModelBadgeId, ModelInfo, ModelType } from './types'

// docs/design/navigation.md, revision 2. Only models wired into the backend are listed.

export type Badge = ModelBadgeId

export interface Feature {
  id: string
  title: string
  description: string
  to: string
  icon: LucideIcon
  disabled?: boolean
  // contract v42: hidden when the person's plan doesn't have this studio
  feature?: FeatureKey
}

export interface Model {
  id: string
  name: string
  badge?: Badge | null
  description: string
  to: string
  mark: string
  // upscale engines: the catalog entry that decides whether it's listed
  catalogId?: string
}

export interface MenuSection {
  title: string
  features: Feature[]
  models: Model[]
  modelsNote?: string
}

export type RailId = 'home' | 'assets' | 'brand' | 'photos' | 'image' | 'video' | 'characters' | '3d' | 'audio' | 'podcast' | 'upscale' | 'queue' | 'settings' | 'admin' | 'print' | 'implant'

export interface RailItem {
  id: RailId
  label: string
  icon: LucideIcon
  to: string
  menu?: MenuSection
  feature?: FeatureKey
}

const IMAGE_MENU: MenuSection = {
  title: 'Image Tools',
  features: [
    { id: 'create', title: 'Create Image', description: 'Generate images from text', to: '/image/generate', icon: Wand2 },
    { id: 'edit', title: 'Edit Image', description: 'Change an image with words', to: '/image/edit', icon: Brush },
    { id: 'img2img', title: 'Image to Image', description: 'Restyle or vary your picture', to: '/image/img2img', icon: Shuffle },
    { id: 'control', title: 'Sketch, depth & pose', description: 'Your layout, a new picture', to: '/image/control', icon: PenLine },
    { id: 'ads', title: 'Ad Studio', description: 'Finished ads with your brand', to: '/image/ads', icon: Megaphone, feature: 'ads' },
    { id: 'refs', title: 'References', description: 'Keep a face, product or style', to: '/image/references', icon: Layers },
    { id: 'upscale', title: 'Image Upscale', description: 'Sharper, bigger, 2× to 4K', to: '/image/upscale', icon: ImageUpscale },
    { id: 'studio', title: 'Photo Studio', description: 'Develop, restore, cut-out', to: '/image/studio', icon: Aperture },
    { id: 'looks', title: 'Looks', description: 'Presets and LUTs, for photos and video', to: '/image/studio?tab=looks', icon: SwatchBook },
    { id: 'templates', title: 'Prompt templates', description: 'Portrait, product, Urdu poster…', to: '/image/prompt-templates', icon: LayoutTemplate },
    { id: 'library', title: 'Library', description: 'All your images', to: '/image/library', icon: Images },
  ],
  models: [
    { id: 'seedvr2', catalogId: 'seedvr2', name: 'SeedVR2 3B / 7B', badge: 'UPSCALE', description: 'Faithful restoration', to: '/image/upscale?engine=best', mark: 'S' },
    { id: 'realesrgan', catalogId: 'esrgan', name: 'Real-ESRGAN ×4', badge: 'QUICK', description: 'Fast enlargement', to: '/image/upscale?engine=quick', mark: 'R' },
  ],
}

const VIDEO_MENU: MenuSection = {
  title: 'Video Tools',
  features: [
    { id: 'create', title: 'Create Video', description: 'One prompt, one clip', to: '/video/create', icon: Video },
    { id: 'img2vid', title: 'Image to Video', description: 'Animate your picture', to: '/video/img2vid', icon: Film },
    { id: 'quick', title: 'Quick Video', description: 'One prompt, a finished video', to: '/video/quick', icon: Wand2 },
    { id: 'projects', title: 'Studio Projects', description: 'Script, cast, storyboard, render', to: '/video/projects', icon: Clapperboard },
    { id: 'poetry', title: 'Poetry Short', description: 'A sung poem with its verses on screen', to: '/video/poetry', icon: Feather, feature: 'poetry' },
    { id: 'presenter', title: 'Presenter Video', description: 'Your character presents a script to camera', to: '/video/presenter', icon: Presentation, feature: 'presenter' },
    { id: 'templates', title: 'Prompt templates', description: 'Product ad, documentary, teaser…', to: '/video/prompt-templates', icon: LayoutTemplate },
    { id: 'upscale', title: 'Video Upscale', description: 'Sharper video, up to 4K', to: '/video/upscale', icon: Maximize2 },
    { id: 'library', title: 'Library', description: 'Every finished video', to: '/video/library', icon: Film },
  ],
  models: [
    { id: 'seedvr2', catalogId: 'seedvr2', name: 'SeedVR2', badge: 'BEST', description: 'Best upscale, steady detail', to: '/video/upscale?engine=best', mark: 'S' },
    { id: 'flashvsr', catalogId: 'flashvsr', name: 'FlashVSR 1.1', badge: 'FAST', description: 'Fast upscale', to: '/video/upscale?engine=fast', mark: 'F' },
  ],
}

// docs/PLAN-m11b-audio-pro.md; Voice is B2 (contract-v16)
const AUDIO_MENU: MenuSection = {
  title: 'Audio Tools',
  features: [
    { id: 'song', title: 'Song', description: 'Style tags and lyrics, sung', to: '/audio/song', icon: Music },
    { id: 'music', title: 'Music & Score', description: 'Instrumental beds and cues', to: '/audio/music', icon: Music4 },
    { id: 'sfx', title: 'Sound Effects', description: 'Foley, ambience, whooshes', to: '/audio/sfx', icon: Volume2 },
    { id: 'speak', title: 'Speak', description: 'A line in any voice, with A/B', to: '/audio/speak', icon: Mic },
    { id: 'voices', title: 'Voices', description: 'Clone from a clip, with consent', to: '/audio/voices', icon: UsersRound },
    { id: 'gallery', title: 'Voice gallery', description: 'Ready-made voices, Urdu and more', to: '/audio/gallery', icon: LibraryBig },
    { id: 'design', title: 'Design a voice', description: 'Describe a voice, keep its seed', to: '/audio/design', icon: Sparkles },
    { id: 'dialogue', title: 'Dialogue', description: 'A cast of voices, line by line', to: '/audio/dialogue', icon: MessagesSquare },
    { id: 'editor', title: 'Editor', description: 'Trim, fade, effects, loudness', to: '/audio/edit', icon: Scissors },
    { id: 'score', title: 'Score a Clip', description: 'Mix music and effects under a video', to: '/audio/score', icon: AudioWaveform },
    { id: 'stems', title: 'Split a song', description: 'Upload a song: get the vocals and the music as separate tracks', to: '/audio/stems', icon: SquareSplitHorizontal },
    { id: 'transcribe', title: 'Transcribe', description: 'Timed text, SRT and VTT subtitles', to: '/audio/transcribe', icon: Captions },
    { id: 'dub', title: 'Dub a video', description: 'Translate and re-voice, Urdu and more', to: '/audio/dub', icon: Languages, feature: 'dub' },
    { id: 'templates', title: 'Prompt templates', description: 'Qawwali, tense score, rain…', to: '/audio/prompt-templates', icon: LayoutTemplate },
    { id: 'library', title: 'Library', description: 'Every song, cue and effect', to: '/audio/library', icon: Library },
  ],
  models: [],
}

// Phase 4: a studio of its own; every episode is audio or video from the start
const PODCAST_MENU: MenuSection = {
  title: 'Podcast Studio',
  features: [
    { id: 'episodes', title: 'Episodes', description: 'Your shows and episodes', to: '/podcast', icon: Podcast },
    { id: 'new-audio', title: 'New audio podcast', description: 'Hosts, script, voices, a mixed episode', to: '/podcast?new=audio', icon: Mic },
    { id: 'new-video', title: 'New video podcast', description: 'The same, then your hosts on camera', to: '/podcast?new=video', icon: Video },
  ],
  models: [],
}

const UPSCALE_MENU: MenuSection = {
  title: 'Upscale',
  features: [
    { id: 'image', title: 'Upscale Image', description: 'Upload or pick from your library', to: '/image/upscale', icon: ImageUpscale },
    { id: 'video', title: 'Upscale Video', description: 'Upload or pick from your library', to: '/video/upscale', icon: Maximize2 },
  ],
  models: [
    { id: 'seedvr2-video', catalogId: 'seedvr2', name: 'SeedVR2 · video', badge: 'BEST', description: 'Sharpest motion detail, the slowest', to: '/video/upscale?engine=best', mark: 'S' },
    { id: 'flashvsr', catalogId: 'flashvsr', name: 'FlashVSR 1.1 · video', badge: 'FAST', description: 'Good detail in a fraction of the time', to: '/video/upscale?engine=fast', mark: 'F' },
    { id: 'zimage-redraw', catalogId: 'zimage_redraw', name: 'Z-Image redraw · image', badge: 'UPSCALE', description: 'Repaints fine texture, adds detail', to: '/image/upscale?engine=redraw', mark: 'Z' },
    { id: 'seedvr2-image', catalogId: 'seedvr2', name: 'SeedVR2 · image', badge: 'BEST', description: 'Restores detail, true to the original', to: '/image/upscale?engine=best', mark: 'S' },
    { id: 'realesrgan', name: 'Real-ESRGAN ×4', badge: 'QUICK', description: 'Seconds per image, clean edges', to: '/image/upscale?engine=quick', mark: 'R' },
  ],
}

const ASSETS_MENU: MenuSection = {
  title: 'Assets',
  features: [
    { id: 'characters', title: 'Characters', description: 'Character Studio: shared cast and sheets', to: '/characters', icon: Users, feature: 'character' },
    { id: 'locations', title: 'Locations', description: 'Places from your projects', to: '/assets#locations', icon: MapPin },
    { id: 'uploads', title: 'Uploads', description: 'Images and videos you brought in', to: '/assets#uploads', icon: FolderUp },
    { id: 'loras', title: 'LoRAs', description: 'Custom styles and faces · later', to: '/assets#loras', icon: Boxes, disabled: true },
  ],
  models: [],
  modelsNote: 'Character LoRAs arrive in a later milestone.',
}

export const RAIL_ITEMS: RailItem[] = [
  { id: 'home', label: 'Home', icon: Home, to: '/' },
  { id: 'assets', label: 'Assets', icon: UserRound, to: '/assets', menu: ASSETS_MENU },
  { id: 'brand', label: 'Brand Kits', icon: Stamp, to: '/brand-kits' },
  { id: 'photos', label: 'Photos', icon: Camera, to: '/photos' },
  { id: 'image', label: 'Image', icon: ImageIcon, to: '/image/generate', menu: IMAGE_MENU, feature: 'image' },
  { id: 'video', label: 'Video', icon: Clapperboard, to: '/video/quick', menu: VIDEO_MENU, feature: 'video' },
  { id: 'audio', label: 'Audio', icon: Music, to: '/audio/song', menu: AUDIO_MENU, feature: 'audio' },
  { id: 'podcast', label: 'Podcast Studio', icon: Podcast, to: '/podcast', menu: PODCAST_MENU, feature: 'podcast' },
  // contract-v25: a studio of its own, not a menu under Video
  { id: 'characters', label: 'Characters', icon: UsersRound, to: '/characters', feature: 'character' },
  // contract v40: picture to 3D model, a studio of its own
  { id: '3d', label: '3D', icon: Box, to: '/3d', feature: '3d' },
  { id: 'upscale', label: 'Upscale', icon: ImageUpscale, to: '/image/upscale', menu: UPSCALE_MENU },
  { id: 'queue', label: 'Queue', icon: ListOrdered, to: '/queue' },
]

export const SETTINGS_ITEM: RailItem = { id: 'settings', label: 'Settings', icon: Settings, to: '/settings' }
// contract v38: only rendered for admins
export const ADMIN_ITEM: RailItem = { id: 'admin', label: 'Admin', icon: ShieldUser, to: '/admin' }
// contract v42: a manager's way into the same section, which shows them only the moderation pages
export const MODERATION_ITEM: RailItem = { id: 'admin', label: 'Moderation', icon: ShieldUser, to: '/admin/review' }
// contract v41: Print Studio, admins only too
export const PRINT_ITEM: RailItem = { id: 'print', label: 'Print Studio', icon: Printer, to: '/admin/print' }
// M22: admins, plus surgeons an admin has granted it to
export const IMPLANT_ITEM: RailItem = { id: 'implant', label: 'Implant Preview', icon: Stethoscope, to: '/clinic/implant' }

const TOOL_ROUTE: Partial<Record<ModelType, string>> = { image: '/image/generate', edit: '/image/edit', video: '/video/create', audio: '/audio/music' }

// one audio catalog serves three pages; the capability says which
const toolRoute = (m: ModelInfo) => {
  if (m.type !== 'audio') return TOOL_ROUTE[m.type]
  return m.capabilities.includes('song') ? '/audio/song' : m.capabilities.includes('sfx') ? '/audio/sfx' : TOOL_ROUTE.audio
}

const navModel = (m: ModelInfo): Model => ({
  id: m.id,
  name: m.label,
  badge: m.badge,
  description: m.description,
  to: `${toolRoute(m)}?model=${encodeURIComponent(m.id)}`,
  mark: m.label.charAt(0).toUpperCase(),
})

// an engine the catalog doesn't mention stays listed (older server); one it marks unavailable goes
const engineListed = (m: Model, upscale: ModelInfo[]) => {
  const entry = upscale.find((u) => u.id === m.catalogId)
  return !entry || entry.available
}

export type Catalog = Record<ModelType, ModelInfo[]>
export type MenuId = 'image' | 'video' | 'audio' | 'podcast' | 'upscale' | 'assets'

/** The Models columns, from the catalog: generation models first, then the upscale engines. Available only. */
export function menusFrom(catalog: Catalog): Record<MenuId, MenuSection> {
  const gen = (...types: ModelType[]) => types.flatMap((t) => catalog[t] ?? []).filter((m) => m.available).map(navModel)
  const engines = (section: MenuSection) => section.models.filter((m) => engineListed(m, catalog.upscale ?? []))
  return {
    image: { ...IMAGE_MENU, models: [...gen('image', 'edit'), ...engines(IMAGE_MENU)] },
    video: { ...VIDEO_MENU, models: [...gen('video'), ...engines(VIDEO_MENU)] },
    audio: { ...AUDIO_MENU, models: gen('audio') },
    podcast: PODCAST_MENU,
    upscale: { ...UPSCALE_MENU, models: engines(UPSCALE_MENU) },
    assets: ASSETS_MENU,
  }
}

export const MENUS = menusFrom(FALLBACK_MODELS)

/** Which rail item lights up for a path. Upscale pages belong to Upscale, not Image/Video. */
export function activeRail(pathname: string): RailId | null {
  const p = pathname.replace(/\/+$/, '') || '/'
  if (p === '/') return 'home'
  if (/^\/(image|video)\/upscale/.test(p)) return 'upscale'
  if (p.startsWith('/image')) return 'image'
  if (p.startsWith('/video') || p.startsWith('/projects') || p === '/create' || p.startsWith('/quick')) return 'video'
  if (p.startsWith('/audio')) return 'audio'
  if (p === '/podcast' || p.startsWith('/podcast/')) return 'podcast'
  if (p.startsWith('/characters')) return 'characters'
  if (p === '/3d' || p.startsWith('/3d/')) return '3d'
  if (p.startsWith('/assets')) return 'assets'
  if (p.startsWith('/photos')) return 'photos'
  if (p.startsWith('/brand-kits')) return 'brand'
  if (p.startsWith('/queue')) return 'queue'
  if (p.startsWith('/settings')) return 'settings'
  if (p.startsWith('/admin/print')) return 'print'
  if (p.startsWith('/clinic/implant')) return 'implant'
  if (p.startsWith('/admin')) return 'admin'
  return null
}

const TITLES: [RegExp, string][] = [
  [/^\/$/, 'Home'],
  [/^\/studio$/, 'What you can make'],
  [/^\/plans$/, 'Plans'],
  [/^\/about$/, 'About'],
  [/^\/(video\/quick|create)$/, 'Quick video'],
  [/^\/quick\//, 'Quick video'],
  [/^\/(video\/)?projects$/, 'Studio projects'],
  [/^\/video\/create/, 'Create video'],
  [/^\/video\/poetry/, 'Poetry Short'],
  [/^\/video\/(prompt-)?templates/, 'Video prompt templates'],
  [/^\/video\/upscale/, 'Video upscale'],
  [/^\/video\/library/, 'Video library'],
  [/^\/image\/generate/, 'Create image'],
  [/^\/image\/edit/, 'Edit image'],
  [/^\/image\/ads/, 'Ad Studio'],
  [/^\/image\/references/, 'References'],
  [/^\/image\/(prompt-)?templates/, 'Image prompt templates'],
  [/^\/image\/upscale/, 'Image upscale'],
  [/^\/image\/library/, 'Image library'],
  [/^\/image\/studio/, 'Photo Studio'],
  [/^\/assets/, 'Assets'],
  [/^\/characters/, 'Character Studio'],
  [/^\/3d\/library$/, '3D library'],
  [/^\/3d(\/|$)/, '3D Studio'],
  [/^\/photos/, 'Photos'],
  [/^\/brand-kits/, 'Brand kits'],
  [/^\/image\/img2img/, 'Image to image'],
  [/^\/image\/control/, 'Sketch, depth and pose to image'],
  [/^\/video\/img2vid/, 'Image to video'],
  [/^\/podcast(\/|$)/, 'Podcast Studio'],
  [/^\/audio\/song/, 'Song'],
  [/^\/audio\/music/, 'Music & score'],
  [/^\/audio\/sfx/, 'Sound effects'],
  [/^\/audio\/(prompt-)?templates/, 'Audio prompt templates'],
  [/^\/audio\/library/, 'Audio library'],
  [/^\/audio\/edit/, 'Audio editor'],
  [/^\/audio\/score/, 'Score a Clip'],
  [/^\/audio\/speak/, 'Speak'],
  [/^\/audio\/voices/, 'Voices'],
  [/^\/audio\/gallery/, 'Voice gallery'],
  [/^\/audio\/design/, 'Design a voice'],
  [/^\/audio\/dialogue/, 'Dialogue'],
  [/^\/audio\/stems/, 'Stems'],
  [/^\/audio\/transcribe/, 'Transcribe'],
  [/^\/audio\/dub/, 'Dub a video'],
  [/^\/queue/, 'Queue'],
  [/^\/settings\/account/, 'Account'],
  [/^\/settings/, 'Settings'],
  [/^\/admin\/users/, 'Users'],
  [/^\/admin\/packages/, 'Packages'],
  [/^\/admin\/subscriptions/, 'Subscriptions'],
  [/^\/admin\/payments/, 'Payments'],
  [/^\/admin\/usage/, 'GPU usage'],
  [/^\/admin\/audit/, 'Audit log'],
  [/^\/admin\/print/, 'Print Studio'],
  [/^\/admin\/bulk/, 'Bulk'],
  [/^\/admin\/rights/, 'Rights'],
  [/^\/admin\/review/, 'Review'],
  [/^\/admin\/people/, 'People'],
  [/^\/admin\/moderation-log/, 'Moderation log'],
  [/^\/clinic\/implant/, 'Implant Preview'],
  [/^\/admin$/, 'Admin'],
]

export const pageTitle = (pathname: string) => TITLES.find(([re]) => re.test(pathname.replace(/(.)\/+$/, '$1')))?.[1] ?? 'Jafat AI Studio'

// contract v42: the studios behind each part of the app. A direct link to one the plan doesn't have shows a
// calm note instead of the page (the server refuses its changes either way). Longest prefix first.
const FEATURE_PATHS: [string, FeatureKey][] = [
  ['/image/ads', 'ads'],
  ['/video/poetry', 'poetry'],
  ['/video/presenter', 'presenter'],
  ['/audio/podcast', 'podcast'],
  ['/podcast', 'podcast'],
  ['/audio/dub', 'dub'],
  ['/characters', 'character'],
  ['/3d', '3d'],
  ['/image/upscale', 'image'],
  ['/video/upscale', 'video'],
  ['/image', 'image'],
  ['/video', 'video'],
  ['/audio', 'audio'],
  ['/admin/print', 'print'],
]

export function featureForPath(pathname: string): FeatureKey | null {
  const p = pathname.replace(/\/+$/, '')
  return FEATURE_PATHS.find(([prefix]) => p === prefix || p.startsWith(prefix + '/'))?.[1] ?? null
}

/** The menus without the studios this person can't use. */
export function allowedMenus(menus: Record<MenuId, MenuSection>, allows: (f?: FeatureKey) => boolean): Record<MenuId, MenuSection> {
  const out = {} as Record<MenuId, MenuSection>
  for (const [id, section] of Object.entries(menus) as [MenuId, MenuSection][]) {
    out[id] = { ...section, features: section.features.filter((f) => allows(f.feature)) }
  }
  return out
}
