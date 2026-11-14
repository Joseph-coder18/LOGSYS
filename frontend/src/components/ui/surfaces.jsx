import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva('inline-flex w-fit items-center gap-1.5 rounded-[4px] border px-1.5 py-0.5 text-xs font-medium', {
  variants: {
    tone: {
      neutral: 'border-border bg-muted text-muted-foreground',
      positive: 'border-success/30 bg-success/10 text-success',
      log: 'border-log/30 bg-log/10 text-log',
      metrics: 'border-metrics/30 bg-metrics/10 text-metrics',
      image: 'border-image/30 bg-image/10 text-image',
      warning: 'border-warning/30 bg-warning/10 text-warning',
    },
  },
  defaultVariants: { tone: 'neutral' },
})

export function Badge({ tone, className, ...props }) {
  return <span data-slot="badge" className={cn(badgeVariants({ tone }), className)} {...props} />
}

export function Card({ className, ...props }) {
  return <section data-slot="card" className={cn('min-w-0 rounded-lg border border-border bg-card', className)} {...props} />
}

export function Alert({ className, ...props }) {
  return <div data-slot="alert" role="alert" className={cn('rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning', className)} {...props} />
}

export function Skeleton({ className }) {
  return <div data-slot="skeleton" aria-hidden="true" className={cn('h-4 animate-pulse rounded bg-muted motion-reduce:animate-none', className)} />
}

export function Textarea({ className, ...props }) {
  return <textarea data-slot="textarea" className={cn('min-h-20 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground disabled:opacity-60', className)} {...props} />
}
