import { type ReactElement } from 'react'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, it, expect } from 'vitest'
import App from './App'
import { MAIN_RESULT_TREE, MAIN_SCAN_BUTTON } from './testids'

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient()
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('App scaffold', () => {
  it('renders the scan button with the correct testid', () => {
    renderWithProviders(<App />)
    expect(screen.getByTestId(MAIN_SCAN_BUTTON)).toBeInTheDocument()
  })

  it('renders the result tree container with the correct testid', () => {
    renderWithProviders(<App />)
    expect(screen.getByTestId(MAIN_RESULT_TREE)).toBeInTheDocument()
  })
})
