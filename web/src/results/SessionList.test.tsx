import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import SessionList from './SessionList'
import { EMPTY_FILTERS } from './filters'
import { summary } from './fixtures'
import type { SessionSummary } from './types'

vi.mock('./download', () => ({ downloadFile: vi.fn() }))
import { downloadFile } from './download'

afterEach(() => {
  cleanup()
  vi.mocked(downloadFile).mockClear()
})

const SESSIONS: SessionSummary[] = [
  summary(),
  summary({
    session_id: 'scenario_2--b',
    created_at: '2026-11-12T15:00:00+00:00',
    scenario_id: 'scenario_2',
    scenario_title: 'Scenario Two',
    mode: 'text',
    display_status: 'fail',
    end_reason: 'max',
    escalation: 10,
  }),
  summary({
    session_id: 'scenario_1--c',
    created_at: '2026-11-14T15:00:00+00:00',
    netid: 'abc123',
    display_status: 'abandoned',
    end_reason: null,
    escalation: 4,
  }),
]

function Harness({ onOpen = () => {} }: { onOpen?: (id: string) => void }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  return (
    <SessionList
      sessions={SESSIONS}
      filters={filters}
      onFiltersChange={setFilters}
      onOpen={onOpen}
    />
  )
}

const ids = (container: HTMLElement) =>
  [...container.querySelectorAll('tbody tr[data-session]')].map((r) =>
    r.getAttribute('data-session'),
  )

describe('SessionList', () => {
  it('lists newest first with outcome badges and mode icons', () => {
    const { container } = render(<Harness />)
    expect(ids(container)).toEqual([
      'scenario_1--c',
      'scenario_2--b',
      'scenario_1--a',
    ])
    expect(
      [...container.querySelectorAll('[data-outcome]')].map(
        (b) => b.textContent,
      ),
    ).toEqual(['–Abandoned', '✗Escalated to max', '✓Success'])
    expect(screen.getByRole('img', { name: 'Text' })).toBeDefined()
  })

  it('narrows by NetID search, scenario, outcome and date', () => {
    const { container } = render(<Harness />)
    fireEvent.change(screen.getByLabelText('NetID'), {
      target: { value: 'ABC' },
    })
    expect(ids(container)).toEqual(['scenario_1--c'])
    fireEvent.change(screen.getByLabelText('NetID'), { target: { value: '' } })

    fireEvent.change(screen.getByLabelText('Scenario'), {
      target: { value: 'scenario_2' },
    })
    expect(ids(container)).toEqual(['scenario_2--b'])
    fireEvent.change(screen.getByLabelText('Scenario'), {
      target: { value: '' },
    })

    fireEvent.change(screen.getByLabelText('Outcome'), {
      target: { value: 'success' },
    })
    expect(ids(container)).toEqual(['scenario_1--a'])
    fireEvent.change(screen.getByLabelText('Outcome'), {
      target: { value: '' },
    })

    fireEvent.change(screen.getByLabelText('From'), {
      target: { value: '2026-11-11' },
    })
    fireEvent.change(screen.getByLabelText('To'), {
      target: { value: '2026-11-13' },
    })
    expect(ids(container)).toEqual(['scenario_2--b'])
  })

  it('sorts by a column and flips on a second click', () => {
    const { container } = render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: /^Agitation/ }))
    expect(ids(container)).toEqual([
      'scenario_1--a',
      'scenario_1--c',
      'scenario_2--b',
    ])
    fireEvent.click(screen.getByRole('button', { name: /^Agitation/ }))
    expect(ids(container)[0]).toBe('scenario_2--b')
  })

  it("a NetID click shows that student's history, oldest first", () => {
    const onOpen = vi.fn()
    const { container } = render(<Harness onOpen={onOpen} />)
    fireEvent.click(container.querySelector('[data-netid="test01"]')!)
    expect(onOpen).not.toHaveBeenCalled()
    expect(ids(container)).toEqual(['scenario_1--a', 'scenario_2--b'])
    const strip = container.querySelector('[data-student="test01"]')!
    expect(strip.textContent).toContain('2 attempts')
    expect(strip.querySelector('svg')!.getAttribute('aria-label')).toBe(
      'Final agitation per attempt: 0, 10',
    )

    fireEvent.click(screen.getByRole('button', { name: /Show all students/ }))
    expect(ids(container)).toHaveLength(3)
  })

  it('opens a session on row click', () => {
    const onOpen = vi.fn()
    const { container } = render(<Harness onOpen={onOpen} />)
    fireEvent.click(
      container.querySelector(
        'tr[data-session="scenario_2--b"] td:nth-child(3)',
      )!,
    )
    expect(onOpen).toHaveBeenCalledWith('scenario_2--b')
  })

  it('downloads exactly the filtered rows as CSV', () => {
    render(<Harness />)
    fireEvent.change(screen.getByLabelText('Outcome'), {
      target: { value: 'max' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
    expect(downloadFile).toHaveBeenCalledTimes(1)
    const [name, text, type] = vi.mocked(downloadFile).mock.calls[0]
    expect(name).toMatch(/^bridge-sessions-\d{4}-\d{2}-\d{2}\.csv$/)
    expect(type).toContain('text/csv')
    const rows = text.trim().split('\r\n')
    expect(rows).toHaveLength(2)
    expect(rows[1]).toContain('"scenario_2--b"')
  })
})
