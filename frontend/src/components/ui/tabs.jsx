import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'
import { cn } from '@/lib/utils'

export function Tabs({ className, ...props }) {
  return <TabsPrimitive.Root data-slot="tabs" className={cn('min-w-0', className)} {...props} />
}

export function TabsList({ className, ...props }) {
  return <TabsPrimitive.List data-slot="tabs-list" className={cn('flex overflow-x-auto border-b border-border', className)} {...props} />
}

export function TabsTrigger({ className, ...props }) {
  return <TabsPrimitive.Tab data-slot="tabs-trigger" className={cn('flex shrink-0 items-center gap-2 border-b-2 border-transparent px-3 py-3 text-sm font-medium text-muted-foreground hover:text-foreground data-active:border-primary data-active:text-primary [&_svg]:size-4', className)} {...props} />
}

export function TabsContent({ className, ...props }) {
  return <TabsPrimitive.Panel data-slot="tabs-content" className={cn('min-w-0 pt-4', className)} {...props} />
}
