import { Button as ButtonPrimitive } from '@base-ui/react/button'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-2 rounded-md border text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground hover:bg-primary/85',
        outline: 'border-border bg-card text-foreground hover:bg-muted',
        ghost: 'border-transparent text-muted-foreground hover:bg-muted hover:text-foreground',
        destructive: 'border-transparent bg-destructive text-white hover:bg-destructive/85',
      },
      size: {
        default: 'h-9 px-3',
        sm: 'h-8 px-2.5',
        icon: 'size-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export function Button({ className, variant, size, type = 'button', ...props }) {
  return <ButtonPrimitive type={type} data-slot="button" className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
