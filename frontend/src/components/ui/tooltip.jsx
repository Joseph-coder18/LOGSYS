import { Tooltip } from '@base-ui/react/tooltip'
import { cn } from '@/lib/utils'

export const TooltipProvider = Tooltip.Provider

export function InfoTooltip({ label, children, className }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger
        type="button"
        className={cn('info-trigger inline-flex items-center gap-1', className)}
      >
        {label}
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Positioner sideOffset={8} className="z-50">
          <Tooltip.Popup className="max-w-[min(16rem,calc(100vw-2rem))] rounded-md border border-border bg-muted px-3 py-2 text-xs leading-relaxed text-foreground shadow-lg">
            {children}
          </Tooltip.Popup>
        </Tooltip.Positioner>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}
