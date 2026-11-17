import {
  OUTCOME_LABEL,
  latency,
  localDay,
  mmss,
  outcomeOf,
  signed,
  timeUsed,
} from './format'

describe('mmss', () => {
  it.each([
    [0, '00:00'],
    [12.9, '00:12'],
    [65, '01:05'],
    [300, '05:00'],
    [-3, '00:00'],
  ])('%s → %s', (t, out) => expect(mmss(t)).toBe(out))
})

describe('signed', () => {
  it('uses a real minus, a plus, and a bare zero', () => {
    expect(signed(-3)).toBe('−3')
    expect(signed(2)).toBe('+2')
    expect(signed(0)).toBe('0')
  })
})

describe('outcomeOf', () => {
  it.each([
    ['success', 'goal', 'Success'],
    ['fail', 'max', 'Escalated to max'],
    ['fail', 'timeout', 'Timed out'],
    ['fail', 'end_action', 'Ended by action'],
    ['fail', null, 'Failed'],
    ['abandoned', 'abandoned', 'Abandoned'],
    ['abandoned', null, 'Abandoned'],
    ['never_connected', null, 'Never connected'],
    ['active', null, 'In progress'],
    ['started', null, 'In progress'],
  ] as const)('%s / %s → %s', (display_status, end_reason, label) => {
    expect(OUTCOME_LABEL[outcomeOf({ display_status, end_reason })]).toBe(label)
  })
})

describe('timeUsed', () => {
  it('shows used / limit, or a dash with no duration', () => {
    expect(timeUsed({ duration_s: 212.4, time_limit_s: 300 })).toBe(
      '03:32 / 05:00',
    )
    expect(timeUsed({ time_limit_s: 300 })).toBe('— / 05:00')
  })
})

describe('latency and localDay', () => {
  it('formats a reply gap to one decimal', () => {
    expect(latency(3.6)).toBe('+3.6 s')
    expect(latency(4)).toBe('+4.0 s')
  })

  it('takes the local calendar day, empty for a bad date', () => {
    const iso = new Date(2026, 10, 3, 23, 30).toISOString()
    expect(localDay(iso)).toBe('2026-11-03')
    expect(localDay('nope')).toBe('')
  })
})
