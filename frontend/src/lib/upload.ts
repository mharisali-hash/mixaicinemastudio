import { API_BASE, ApiError } from './api'
import type { BrandPurpose } from './brand'
import type { Character, MediaItem, MediaKind } from './types'

const MB = 1024 * 1024

// what can be uploaded; 3D models are made in the studio, not brought in (yet)
export type UploadKind = Exclude<MediaKind, 'model'>

// contract-v5: the server checks the bytes too; this just saves a pointless 2 GB upload
export const UPLOAD_RULES: Record<UploadKind, { types: string[]; exts: string[]; maxBytes: number; label: string }> = {
  image: { types: ['image/png', 'image/jpeg', 'image/webp'], exts: ['png', 'jpg', 'jpeg', 'webp'], maxBytes: 40 * MB, label: 'PNG, JPG or WebP up to 40 MB' },
  video: { types: ['video/mp4', 'video/quicktime', 'video/webm'], exts: ['mp4', 'mov', 'webm'], maxBytes: 2048 * MB, label: 'MP4, MOV or WebM up to 2 GB' },
  // contract-v14; .webm stays a video by name, audio/webm is told apart by its type
  audio: {
    types: ['audio/wav', 'audio/x-wav', 'audio/wave', 'audio/mpeg', 'audio/mp4', 'audio/x-m4a', 'audio/aac', 'audio/ogg', 'audio/opus', 'audio/flac', 'audio/x-flac', 'audio/webm'],
    exts: ['wav', 'mp3', 'm4a', 'aac', 'ogg', 'opus', 'flac'],
    maxBytes: 200 * MB,
    label: 'WAV, MP3, M4A, OGG or FLAC up to 200 MB',
  },
}

export const acceptFor = (kinds: UploadKind[]) => kinds.flatMap((k) => [...UPLOAD_RULES[k].types, ...UPLOAD_RULES[k].exts.map((e) => `.${e}`)]).join(',')

const KINDS = ['image', 'video', 'audio'] as const

export function kindOfFile(file: File): UploadKind | null {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  // the browser's type first: an .m4a is audio/mp4 and a voice note can be audio/webm
  return KINDS.find((k) => UPLOAD_RULES[k].types.includes(file.type)) ?? KINDS.find((k) => UPLOAD_RULES[k].exts.includes(ext)) ?? null
}

const sizeText = (bytes: number) => (bytes >= 1024 * MB ? `${(bytes / 1024 / MB).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / MB))} MB`)

/** Plain-language reason a file can't go up, or null when it looks fine. */
export function checkFile(file: File, allowed: UploadKind[]): string | null {
  const kind = kindOfFile(file)
  if (!kind || !allowed.includes(kind)) {
    return `“${file.name}” isn't a file type we can use. Try ${allowed.map((k) => UPLOAD_RULES[k].label).join(', or ')}.`
  }
  const rule = UPLOAD_RULES[kind]
  if (file.size > rule.maxBytes) return `“${file.name}” is ${sizeText(file.size)}, bigger than the ${sizeText(rule.maxBytes)} limit for ${kind === 'audio' ? 'audio' : `${kind}s`}.`
  return null
}

export function uploadErrorText(status: number, detail: string | undefined, fileName: string): string {
  if (status === 413) return `“${fileName}” is too big for the server. Images can be up to 40 MB, videos up to 2 GB, audio up to 200 MB.`
  if (status === 415) return `“${fileName}” isn't a supported image, video or audio file, even if its name says so. Try PNG, JPG, WebP, MP4, MOV, WebM, WAV, MP3 or FLAC.`
  if (status === 0) return "Can't reach the studio server. Check your connection and try again."
  if (status === 401) return 'Your session has ended. Sign in again, then retry the upload.'
  if (status >= 500) return 'The server hit a problem while saving the file. Try again in a moment.'
  return detail || `The upload didn't go through (${status}).`
}

/** Pasted files: screenshots arrive as items rather than files in some browsers. */
export function filesFromClipboard(data: DataTransfer | null): File[] {
  if (!data) return []
  if (data.files?.length) return Array.from(data.files)
  return Array.from(data.items ?? [])
    .filter((i) => i.kind === 'file')
    .map((i) => i.getAsFile())
    .filter((f): f is File => !!f)
}

export interface UploadHandle<T = MediaItem> {
  promise: Promise<T>
  abort: () => void
}

type ErrorText = (status: number, detail: string | undefined, fileName: string) => string

/** XHR rather than fetch: fetch still can't report upload progress. */
export function xhrUpload<T>(path: string, file: File, onProgress: (fraction: number) => void, errorText: ErrorText, fields: Record<string, string | undefined> = {}): UploadHandle<T> {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<T>((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    for (const [k, v] of Object.entries(fields)) if (v) form.append(k, v)
    xhr.open('POST', `${API_BASE}${path}`)
    xhr.withCredentials = true
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onerror = () => reject(new ApiError(0, errorText(0, undefined, file.name)))
    xhr.onabort = () => reject(new ApiError(0, 'Upload cancelled.'))
    xhr.onload = () => {
      let body: unknown
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        body = null
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(body as T)
      const detail = (body as { detail?: unknown } | null)?.detail
      reject(new ApiError(xhr.status, errorText(xhr.status, typeof detail === 'string' ? detail : undefined, file.name), body))
    }
    xhr.send(form)
  })
  return { promise, abort: () => xhr.abort() }
}

// contract-v25: a character pack exported from Character Studio
export const uploadCharacterPack = (file: File, onProgress: (fraction: number) => void) =>
  xhrUpload<Character>('/characters/import', file, onProgress, (status, detail, name) =>
    status === 413 ? `“${name}” is bigger than the 300 MB pack limit.` : detail || uploadErrorText(status, detail, name))

export const uploadMedia = (file: File, onProgress: (fraction: number) => void, title?: string) =>
  xhrUpload<MediaItem>('/media/upload', file, onProgress, uploadErrorText, { title })

// contract v11: the photo catalogue also takes camera RAW, HEIC and TIFF (kept as the original, shown as a JPEG)
export const RAW_EXTS = ['nef', 'nrw', 'cr2', 'cr3', 'arw', 'srf', 'sr2', 'raf', 'orf', 'rw2', 'dng', 'pef', 'srw', '3fr', 'iiq', 'erf', 'x3f', 'kdc', 'mrw', 'rwl']
export const PHOTO_EXTS = ['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif', 'hif', 'tif', 'tiff', ...RAW_EXTS]
export const PHOTO_ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'image/tiff', ...PHOTO_EXTS.map((e) => `.${e}`)].join(',')

export function checkPhoto(file: File): string | null {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!PHOTO_EXTS.includes(ext)) return `“${file.name}” isn't a photo we can import. Try JPEG, PNG, WebP, HEIC, TIFF or a camera RAW file.`
  const big = ['jpg', 'jpeg', 'png', 'webp'].includes(ext) ? 40 * MB : 250 * MB
  if (file.size > big) return `“${file.name}” is ${sizeText(file.size)}, bigger than the ${sizeText(big)} limit for this kind of file.`
  return null
}

export const uploadPhoto = (file: File, onProgress: (fraction: number) => void, fields: { album_id?: string; on_duplicate?: 'skip' | 'keep' }) =>
  xhrUpload<MediaItem>('/media/upload', file, onProgress, (status, detail, name) =>
    status === 415 && detail ? `“${name}”: ${detail}` : uploadErrorText(status, detail, name), fields)

// contract-v7: brand assets go through their own endpoint, which sniffs each purpose differently
export const BRAND_RULES: Record<BrandPurpose, { exts: string[]; types: string[]; maxBytes: number; label: string }> = {
  logo: { exts: ['png', 'webp', 'svg'], types: ['image/png', 'image/webp', 'image/svg+xml'], maxBytes: 5 * MB, label: 'PNG, WebP or SVG up to 5 MB' },
  font: { exts: ['ttf', 'otf'], types: ['font/ttf', 'font/otf', 'font/sfnt', 'application/x-font-ttf', 'application/font-sfnt'], maxBytes: 10 * MB, label: 'TTF or OTF up to 10 MB' },
  product: { ...UPLOAD_RULES.image },
  reference: { ...UPLOAD_RULES.image },
}

export const brandAccept = (p: BrandPurpose) => [...BRAND_RULES[p].types, ...BRAND_RULES[p].exts.map((e) => `.${e}`)].join(',')

/** Brand-specific refusal in plain words, or null. Extensions win: browsers rarely know a font's MIME type. */
export function checkBrandFile(file: File, purpose: BrandPurpose): string | null {
  const rule = BRAND_RULES[purpose]
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (purpose === 'logo' && (ext === 'jpg' || ext === 'jpeg' || file.type === 'image/jpeg')) {
    return `“${file.name}” is a JPEG, which can't be see-through. Export the logo as PNG or SVG.`
  }
  // the server insists the name's extension matches the bytes, so don't guess from the MIME type
  if (!rule.exts.includes(ext)) {
    return `“${file.name}” isn't a file type we can use here. Try ${rule.label}.`
  }
  if (file.size > rule.maxBytes) return `“${file.name}” is ${sizeText(file.size)}, bigger than the ${sizeText(rule.maxBytes)} limit.`
  return null
}

export const brandUploadErrorText =
  (purpose: BrandPurpose): ErrorText =>
  (status, detail, fileName) => {
    if (status === 413) return `“${fileName}” is too big for the server. The limit is ${BRAND_RULES[purpose].label.replace(/^.* up to /, '')}.`
    if (status === 415) {
      return purpose === 'font'
        ? `“${fileName}” isn't a working TTF or OTF font, even if its name says so.`
        : purpose === 'logo'
          ? `“${fileName}” couldn't be used as a logo. Try a PNG with transparency, or a plain SVG without scripts or links.`
          : `“${fileName}” isn't a supported image, even if its name says so. Try PNG, JPG or WebP.`
    }
    return uploadErrorText(status, detail, fileName)
  }

export const uploadBrandAsset = (file: File, purpose: BrandPurpose, onProgress: (fraction: number) => void) =>
  xhrUpload<{ item: MediaItem; warnings: string[] }>(`/brand-kits/assets?purpose=${purpose}`, file, onProgress, brandUploadErrorText(purpose))
