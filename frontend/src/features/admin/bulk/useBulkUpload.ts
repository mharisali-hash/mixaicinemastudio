import { useCallback, useEffect, useRef, useState } from 'react'
import { bulkApi, isPicture, pathOf, uploadBulkFile } from './api'

const CONCURRENCY = 4
// the "what's already there" question goes in slices so one request body stays small
const CHECK_SLICE = 2000

export interface UploadState {
  total: number
  sent: number
  skipped: number
  ignored: number
  failed: { name: string; error: string }[]
  running: boolean
  checking: boolean
}

const idle: UploadState = { total: 0, sent: 0, skipped: 0, ignored: 0, failed: [], running: false, checking: false }

/**
 * Thousands of pictures, four at a time. Only the File handles are kept (the browser reads each one as it
 * goes up), never previews, so a 5,000-picture folder costs next to nothing in memory. Counts, not a row
 * per file, for the same reason. A dropped connection resumes by picking the same folder again: the server
 * says which names it already has and those are skipped.
 */
export function useBulkUpload(batchId: string, onSettled: () => void) {
  const [state, setState] = useState<UploadState>(idle)
  const queue = useRef<File[]>([])
  const active = useRef(0)
  const aborts = useRef(new Set<() => void>())
  const stopped = useRef(false)
  const failedFiles = useRef<File[]>([])
  const settled = useRef(onSettled)
  useEffect(() => {
    settled.current = onSettled
  }, [onSettled])

  // the uploads' callbacks start the next one through this, not through pump itself
  const again = useRef<() => void>(() => {})
  const pump = useCallback(() => {
    while (!stopped.current && active.current < CONCURRENCY && queue.current.length) {
      const file = queue.current.shift()!
      active.current += 1
      const h = uploadBulkFile(batchId, file, pathOf(file))
      aborts.current.add(h.abort)
      h.promise
        .then((r) => setState((s) => (r.duplicate ? { ...s, skipped: s.skipped + 1 } : { ...s, sent: s.sent + 1 })))
        .catch((e: Error) => {
          if (stopped.current) return
          failedFiles.current.push(file)
          setState((s) => ({ ...s, failed: [...s.failed, { name: pathOf(file), error: e.message }] }))
        })
        .finally(() => {
          active.current -= 1
          aborts.current.delete(h.abort)
          if (!queue.current.length && active.current === 0) {
            setState((s) => ({ ...s, running: false }))
            settled.current()
          } else again.current()
        })
    }
  }, [batchId])
  useEffect(() => {
    again.current = pump
  }, [pump])

  const start = useCallback(
    async (picked: File[]) => {
      const pictures = picked.filter(isPicture)
      stopped.current = false
      setState({ ...idle, total: pictures.length, ignored: picked.length - pictures.length, checking: true, running: true })
      const have = new Set<string>()
      try {
        for (let i = 0; i < pictures.length; i += CHECK_SLICE) {
          const slice = pictures.slice(i, i + CHECK_SLICE).map((f) => ({ name: pathOf(f), size: f.size }))
          for (const n of (await bulkApi.check(batchId, slice)).have) have.add(n)
        }
      } catch {
        // can't ask: send everything, the server still refuses to store a file twice
      }
      const todo = pictures.filter((f) => !have.has(pathOf(f)))
      setState((s) => ({ ...s, skipped: pictures.length - todo.length, checking: false, running: todo.length > 0 }))
      if (!todo.length) return settled.current()
      queue.current.push(...todo)
      pump()
    },
    [batchId, pump],
  )

  const retryFailed = useCallback(() => {
    const again = failedFiles.current
    failedFiles.current = []
    stopped.current = false
    setState((s) => ({ ...s, failed: [], running: again.length > 0 }))
    queue.current.push(...again)
    pump()
  }, [pump])

  const stop = useCallback(() => {
    stopped.current = true
    queue.current = []
    for (const abort of aborts.current) abort()
    setState((s) => ({ ...s, running: false }))
  }, [])

  // leaving mid-upload would silently drop the rest: ask first
  useEffect(() => {
    if (!state.running) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [state.running])

  useEffect(() => () => stop(), [stop])

  return { state, start, retryFailed, stop }
}
