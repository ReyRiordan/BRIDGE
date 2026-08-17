import { render, screen } from '@testing-library/react'
import App from './App'

describe('App', () => {
  it('renders the placeholder shell', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'BRIDGE' })).toBeDefined()
  })
})
