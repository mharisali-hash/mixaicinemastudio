import { request } from './api'
import type { Generation } from './types'

// Character Looks: a character's outfits, settings and poses, each with its own picture
export interface CharacterLook {
  id: string
  character_id: string
  name: string
  outfit: string
  setting: string
  pose: string
  prompt: string
  is_default: boolean
  sort: number
  picture: Generation | null
  created_at: string
}

export interface LookPack {
  id: LookPackId
  label: string
  looks: string[]
}

export type LookPackId = 'business' | 'casual' | 'traditional' | 'presenter' | 'outdoor'

export interface LookIn {
  name: string
  outfit?: string
  setting?: string
  pose?: string
  prompt?: string
  make?: boolean
}

export type Expressiveness = 'calm' | 'natural' | 'lively'
export interface Performance {
  expressiveness?: Expressiveness | null
  gestures?: string
}

export const EXPRESSIVENESS: { value: Expressiveness; label: string; hint: string }[] = [
  { value: 'calm', label: 'Calm', hint: 'Composed, very few gestures' },
  { value: 'natural', label: 'Natural', hint: 'Small, everyday gestures' },
  { value: 'lively', label: 'Lively', hint: 'Expressive face, animated hands' },
]

const base = (cid: string) => `/characters/${cid}/looks`

export const looksApi = {
  list: (cid: string) => request<{ looks: CharacterLook[]; packs: LookPack[] }>('GET', base(cid)),
  add: (cid: string, body: LookIn) => request<CharacterLook>('POST', base(cid), body),
  pack: (cid: string, pack: LookPackId) => request<{ looks: CharacterLook[] }>('POST', `${base(cid)}/pack`, { pack }),
  update: (cid: string, lid: string, body: Partial<Pick<CharacterLook, 'name' | 'outfit' | 'setting' | 'pose' | 'prompt' | 'is_default'>>) =>
    request<CharacterLook>('PATCH', `${base(cid)}/${lid}`, body),
  remove: (cid: string, lid: string) => request<void>('DELETE', `${base(cid)}/${lid}`),
  generate: (cid: string, lid: string, note = '') => request<CharacterLook>('POST', `${base(cid)}/${lid}/generate`, { note }),
}

export const lookBusy = (l: CharacterLook) => !!l.picture && ['queued', 'generating'].includes(l.picture.status)

export const defaultLook = (looks: CharacterLook[] | undefined) => looks?.find((l) => l.is_default) ?? looks?.[0] ?? null
