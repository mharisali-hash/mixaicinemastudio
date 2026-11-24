import { useLayoutEffect } from 'react'

// A textarea as tall as its text, so the page keeps the only scrollbar. Typing, paste and outside changes
// (enhance, templates, reuse) all arrive as a new value; a width change rewraps the lines, so watch that too.
export function useAutoGrow(ref: React.RefObject<HTMLTextAreaElement | null>, value: unknown, on = true) {
  useLayoutEffect(() => {
    if (on && ref.current) fit(ref.current)
  }, [ref, value, on])

  useLayoutEffect(() => {
    const el = ref.current
    if (!on || !el || typeof ResizeObserver === 'undefined') return
    let width = el.offsetWidth
    const ro = new ResizeObserver(() => {
      // our own height change fires this too
      if (el.offsetWidth === width) return
      width = el.offsetWidth
      fit(el)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, on])
}

function fit(el: HTMLTextAreaElement) {
  // collapsing to measure can shorten the page for a moment and make the scroller jump
  const scroller = el.closest('main') ?? document.scrollingElement
  const top = scroller?.scrollTop ?? 0
  el.style.overflowY = 'hidden'
  el.style.height = 'auto'
  // scrollHeight leaves out the border; without it the last line sits 2 px under the edge
  el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`
  if (scroller && scroller.scrollTop !== top) scroller.scrollTop = top
}
