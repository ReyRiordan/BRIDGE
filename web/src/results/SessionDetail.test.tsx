import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import SessionDetail from './SessionDetail'
import { SCENARIO, record } from './fixtures'

vi.mock('./download', () => ({ downloadFile: vi.fn() }))
import { downloadFile } from './download'

afterEach(cleanup)

describe('SessionDetail', () => {
  it('renders the header facts and the end reason', () => {
    render(<SessionDetail record={record()} scenario={SCENARIO} />)
    expect(screen.getByRole('heading', { level: 2 }).textContent).toContain(
      'test01',
    )
    expect(screen.getByText('Success')).toBeDefined()
    expect(screen.getByText('Agitation reached the goal.')).toBeDefined()
    expect(screen.getByText('03:32 / 05:00')).toBeDefined()
  })

  it('prefers the recorded debrief as the reason', () => {
    render(
      <SessionDetail
        record={record({
          display_status: 'fail',
          end_reason: 'end_action',
          reason: 'You called security.',
        })}
        scenario={SCENARIO}
      />,
    )
    expect(screen.getByText('You called security.')).toBeDefined()
    expect(screen.getByText('Ended by action')).toBeDefined()
  })

  it('annotates the transcript with chips, repeats, the text marker and latency', () => {
    const { container } = render(
      <SessionDetail record={record()} scenario={SCENARIO} />,
    )
    const chips = [...container.querySelectorAll('[data-chip]')].map((c) => [
      c.textContent,
      c.getAttribute('data-chip'),
    ])
    expect(chips).toEqual([
      ['Acknowledge autonomy −3', 'good'],
      ['Explain risks −2', 'good'],
      ['Acknowledge autonomy · repeat 0', 'repeat'],
    ])
    const turns = container.querySelectorAll('li[data-role]')
    expect(turns[0].querySelector('[data-source="text"]')).toBeNull()
    expect(turns[2].querySelector('[data-source="text"]')).not.toBeNull()
    expect(turns[0].textContent).toContain('00:12')
    expect(
      [...container.querySelectorAll('[data-latency]')].map(
        (l) => l.textContent,
      ),
    ).toEqual(['+3.6 s', '+4.5 s'])
  })

  it('marks every scenario action earned, attempted, missed, occurred or avoided', () => {
    const r = record()
    r.turns.push({
      role: 'student',
      t: 50,
      at: '',
      content: 'Or else.',
      actions: [
        { type: 'Threaten', point_change: 2, repeat: false },
        { type: 'Offer food', point_change: 0, repeat: false },
      ],
    })
    r.turns.push({
      role: 'student',
      t: 60,
      at: '',
      content: 'I said or else.',
      actions: [{ type: 'Threaten', point_change: 2, repeat: false }],
    })
    const { container } = render(
      <SessionDetail record={r} scenario={SCENARIO} />,
    )
    const row = (type: string) =>
      container.querySelector(`li[data-action="${type}"]`)!
    expect(row('Acknowledge autonomy').getAttribute('data-state')).toBe(
      'earned',
    )
    expect(row('Acknowledge autonomy').textContent).toContain(
      'Earned at 00:12, −3 · repeated 1×',
    )
    expect(row('Explain risks').getAttribute('data-state')).toBe('earned')
    expect(row('Offer food').getAttribute('data-state')).toBe('attempted')
    expect(row('Threaten').getAttribute('data-state')).toBe('occurred')
    expect(row('Threaten').textContent).toContain(
      'Occurred 2× (first at 00:50), +4',
    )
    expect(row('Call security').getAttribute('data-state')).toBe('avoided')
  })

  it('shows a missed action and the scenario error when it cannot load', () => {
    const { container, rerender } = render(
      <SessionDetail record={record()} scenario={SCENARIO} />,
    )
    expect(
      container
        .querySelector('li[data-action="Offer food"]')!
        .getAttribute('data-state'),
    ).toBe('missed')
    rerender(
      <SessionDetail
        record={record()}
        scenario={null}
        scenarioError="No scenario."
      />,
    )
    expect(screen.getByText('No scenario.')).toBeDefined()
  })

  it('downloads the raw item as JSON', () => {
    const r = record()
    render(<SessionDetail record={r} scenario={SCENARIO} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }))
    expect(downloadFile).toHaveBeenCalledWith(
      'scenario_1--a.json',
      JSON.stringify(r, null, 2),
      'application/json',
    )
  })
})
