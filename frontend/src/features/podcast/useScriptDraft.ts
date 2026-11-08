import { useCallback, useEffect, useRef, useState } from 'react'
import { usePatchScript } from '@/hooks/useDialogue'
import { mergeServer, toPatch, type CastMember, type Script, type ScriptLine } from '@/lib/dialogue'

const SAVE_AFTER_MS = 600

type Draft = { cast: CastMember[]; lines: ScriptLine[] }

/** The Dialogue editor's draft: edits show at once and save a moment later; the server's bookkeeping
 * (status, takes) keeps flowing in under what was typed. */
export function useScriptDraft(script: Script) {
  const save = usePatchScript(script.id)
  const [local, setLocal] = useState<Draft>({ cast: script.cast, lines: script.lines })
  const [ver, setVer] = useState(0)
  const [savedVer, setSavedVer] = useState(0)
  const dirty = ver !== savedVer
  const [seen, setSeen] = useState(script)
  if (seen !== script) {
    setSeen(script)
    setLocal((l) => ({ cast: dirty ? l.cast : script.cast, lines: mergeServer(l.lines, script.lines, dirty) }))
  }

  const latest = useRef(local)
  useEffect(() => {
    latest.current = local
  })
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { mutateAsync } = save
  const saveNow = useCallback(
    async (v: number) => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
      const { cast, lines } = latest.current
      await mutateAsync({ cast, lines: lines.map(toPatch) })
      setSavedVer((s) => Math.max(s, v))
    },
    [mutateAsync],
  )
  useEffect(() => {
    if (ver === savedVer) return
    timer.current = setTimeout(() => saveNow(ver).catch(() => {}), SAVE_AFTER_MS)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [ver, savedVer, saveNow])

  const edit = (fn: (d: Draft) => Draft) => {
    setLocal(fn)
    setVer((v) => v + 1)
  }
  const flush = () => (dirty ? saveNow(ver) : Promise.resolve())
  return { lines: mergeServer(local.lines, script.lines, true), cast: local.cast, edit, flush, dirty, error: save.error }
}
