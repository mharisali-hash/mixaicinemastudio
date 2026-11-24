import * as React from 'react'
import { useAutoGrow } from '@/hooks/useAutoGrow'
import { cn } from '@/lib/utils'
import { fieldClass } from './input'

interface Props extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  // prompts: grow with the text instead of scrolling inside (controlled fields only)
  autoGrow?: boolean
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, Props>(({ className, autoGrow = false, ...props }, ref) => {
  const own = React.useRef<HTMLTextAreaElement>(null)
  React.useImperativeHandle(ref, () => own.current as HTMLTextAreaElement)
  useAutoGrow(own, props.value, autoGrow)
  return <textarea ref={own} className={cn(fieldClass, 'min-h-20 py-2', autoGrow ? 'resize-none' : 'resize-y', className)} {...props} />
})
Textarea.displayName = 'Textarea'
