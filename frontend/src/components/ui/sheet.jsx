import { Dialog } from '@base-ui/react/dialog'
import { IconX } from '@tabler/icons-react'
import { cn } from '@/lib/utils'

export function Sheet({ open, onOpenChange, children }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </Dialog.Root>
  )
}

export function SheetContent({ title, subtitle, children, className }) {
  return (
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px] transition-opacity" />
      <Dialog.Popup
        className={cn(
          'fixed inset-y-0 right-0 z-50 flex w-full max-w-[460px] flex-col border-l border-border bg-[var(--bg-sidebar)] text-foreground shadow-2xl outline-none',
          className,
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <Dialog.Title className="truncate text-sm font-semibold">{title}</Dialog.Title>
            {subtitle && <p className="truncate font-mono text-[11px] text-muted-foreground">{subtitle}</p>}
          </div>
          <Dialog.Close
            aria-label="Close inspector"
            className="inline-flex size-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <IconX className="size-4" stroke={1.75} />
          </Dialog.Close>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </Dialog.Popup>
    </Dialog.Portal>
  )
}

