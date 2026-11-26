import React from 'react'
import ReactDOM from 'react-dom/client'
import { ChakraProvider, createSystem, defaultConfig } from '@chakra-ui/react'
import App from './App.jsx'
import { TooltipProvider } from '@/components/ui/tooltip'
import './index.css'

// Scoped Chakra UI v3 system with preflight disabled so Tailwind v4 + shadcn/ui styles remain intact.
const chakraSystem = createSystem(defaultConfig, {
  preflight: false,
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ChakraProvider value={chakraSystem}>
      <TooltipProvider delay={350}>
        <App />
      </TooltipProvider>
    </ChakraProvider>
  </React.StrictMode>,
)
