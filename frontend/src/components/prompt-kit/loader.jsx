import { cn } from '@/lib/utils'

const typingSizes = {
  sm: 'gap-1 [&>span]:size-1',
  md: 'gap-1.5 [&>span]:size-1.5',
  lg: 'gap-2 [&>span]:size-2',
}

export function Loader({ size = 'md', className }) {
  return (
    <span aria-hidden="true" className={cn('inline-flex items-center', typingSizes[size], className)}>
      <span className="typing-dot" />
      <span className="typing-dot" />
      <span className="typing-dot" />
    </span>
  )
}
