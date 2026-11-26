import { useEffect, useRef } from 'react'
import { cn } from '@/lib/utils'

export function Popover({ open, onOpenChange, trigger, children, className }) {
  const containerRef = useRef(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(event) {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        onOpenChange(false)
      }
    }
    function onKeyDown(event) {
      if (event.key === 'Escape') onOpenChange(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open, onOpenChange])

  return (
    <div ref={containerRef} className="relative inline-block">
      {trigger}
      {open && (
        <div
          className={cn(
            'absolute bottom-full left-0 z-30 mb-2 w-72 rounded-xl border border-border bg-[var(--bg-elevated)] p-3 text-foreground shadow-xl',
            className,
          )}
        >
          {children}
        </div>
      )}
    </div>
  )
}

