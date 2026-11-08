import {
  Captions,
  Clapperboard,
  Languages,
  LayoutTemplate,
  Library,
  LibraryBig,
  Mic,
  MessagesSquare,
  Music,
  Music4,
  Scissors,
  Shuffle,
  Sparkles,
  SquareSplitHorizontal,
  UsersRound,
  Volume2,
  type LucideIcon,
} from 'lucide-react'

// docs/PLAN-m11b-audio-pro.md "Look and feel": the Audio workspace sidebar

export interface AudioPage {
  id: string
  label: string
  to: string
  icon: LucideIcon
  // built in a later phase; listed so the room's shape is visible from day one
  soon?: boolean
  // which paths light this entry up, when more than `to` itself
  match?: RegExp
}

export interface AudioGroup {
  id: string
  label: string
  pages: AudioPage[]
}

export const AUDIO_GROUPS: AudioGroup[] = [
  {
    id: 'create',
    label: 'Create',
    pages: [
      { id: 'song', label: 'Song', to: '/audio/song', icon: Music },
      { id: 'music', label: 'Music & Score', to: '/audio/music', icon: Music4 },
      { id: 'sfx', label: 'Sound FX', to: '/audio/sfx', icon: Volume2 },
    ],
  },
  {
    id: 'voice',
    label: 'Voice',
    pages: [
      { id: 'speak', label: 'Speak', to: '/audio/speak', icon: Mic },
      { id: 'voices', label: 'Voices', to: '/audio/voices', icon: UsersRound },
      { id: 'gallery', label: 'Gallery', to: '/audio/gallery', icon: LibraryBig },
      { id: 'design', label: 'Design', to: '/audio/design', icon: Sparkles },
    ],
  },
  {
    id: 'story',
    label: 'Story',
    pages: [
      { id: 'dialogue', label: 'Dialogue', to: '/audio/dialogue', icon: MessagesSquare },
    ],
  },
  {
    id: 'edit',
    label: 'Edit',
    pages: [
      { id: 'editor', label: 'Editor', to: '/audio/edit', icon: Scissors, match: /^\/audio\/edit(\/|$)/ },
      { id: 'score', label: 'Score a Clip', to: '/audio/score', icon: Clapperboard },
      { id: 'remix', label: 'Remix', to: '/audio/remix', icon: Shuffle },
    ],
  },
  {
    id: 'tools',
    label: 'Tools',
    pages: [
      { id: 'stems', label: 'Stems', to: '/audio/stems', icon: SquareSplitHorizontal },
      { id: 'transcribe', label: 'Transcribe', to: '/audio/transcribe', icon: Captions },
      { id: 'dub', label: 'Dub', to: '/audio/dub', icon: Languages },
    ],
  },
  {
    id: 'library',
    label: 'Library',
    pages: [
      { id: 'library', label: 'Library', to: '/audio/library', icon: Library },
      { id: 'templates', label: 'Templates', to: '/audio/prompt-templates', icon: LayoutTemplate, match: /^\/audio\/(prompt-)?templates/ },
    ],
  },
]

/** The pages Ctrl+1…9 reach, in sidebar order; the ones still to come are skipped. */
export const NUMBERED: AudioPage[] = AUDIO_GROUPS.flatMap((g) => g.pages.filter((p) => !p.soon)).slice(0, 9)

export const shortcutOf = (p: AudioPage) => {
  const i = NUMBERED.indexOf(p)
  return i < 0 ? null : i + 1
}

export const isActive = (p: AudioPage, pathname: string) => (p.match ? p.match.test(pathname) : pathname === p.to || pathname.startsWith(`${p.to}/`))

