import type { Scenario } from '../types/scenario'
import type { SessionRecord, SessionSummary } from './types'

/** Test fixtures shaped like the results API's responses. */

export function summary(
  overrides: Partial<SessionSummary> = {},
): SessionSummary {
  return {
    session_id: 'scenario_1--a',
    created_at: '2026-11-10T15:00:00+00:00',
    netid: 'test01',
    scenario_id: 'scenario_1',
    scenario_title: 'Scenario One',
    mode: 'voice',
    status: 'success',
    display_status: 'success',
    end_reason: 'goal',
    start_escalation: 5,
    escalation: 0,
    max_escalation: 10,
    time_limit_s: 300,
    started_at: '2026-11-10T15:00:05+00:00',
    duration_s: 212.4,
    helpful_count: 3,
    escalating_count: 1,
    updated_at: '2026-11-10T15:04:00+00:00',
    ...overrides,
  }
}

export function record(overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    ...summary(),
    reason: null,
    goal: 0,
    turns: [
      {
        role: 'student',
        t: 12.3,
        at: '2026-11-10T15:00:17+00:00',
        content: 'I hear that you want to go home.',
        source: 'voice',
        actions: [
          { type: 'Acknowledge autonomy', point_change: -3, repeat: false },
        ],
        escalation_after: 2,
      },
      {
        role: 'patient',
        t: 15.9,
        at: '2026-11-10T15:00:21+00:00',
        content: 'Then let me go.',
      },
      {
        role: 'student',
        t: 40,
        at: '2026-11-10T15:00:45+00:00',
        content: 'You could get very sick.',
        source: 'text',
        actions: [
          { type: 'Explain risks', point_change: -2, repeat: false },
          { type: 'Acknowledge autonomy', point_change: 0, repeat: true },
        ],
        escalation_after: 0,
      },
      {
        role: 'patient',
        t: 44.5,
        at: '2026-11-10T15:00:50+00:00',
        content: 'Fine.',
      },
    ],
    timeline: [
      { t: 0, escalation: 5, cause: 'start' },
      { t: 12.3, escalation: 2, cause: 'turn' },
      { t: 30, escalation: 3, cause: 'inactivity' },
      { t: 40, escalation: 0, cause: 'turn' },
    ],
    ...overrides,
  }
}

const action = (type: string, point_change: number, extra = {}) => ({
  type,
  desc: `${type} desc`,
  point_change,
  persist: false,
  layer: null,
  active: null,
  inactive: null,
  ...extra,
})

export const SCENARIO: Scenario = {
  id: 'scenario_1',
  title: 'Scenario One',
  intro_image: 'intro.png',
  intro: '',
  goal: '',
  time_limit: 300,
  point_bar: { max: 10, start: 5, goal: 0 },
  actions: [
    action('Acknowledge autonomy', -3),
    action('Explain risks', -2),
    action('Offer food', -1),
    action('Threaten', 2),
    action('Call security', 0, { ends_simulation: true, debrief: 'Ended.' }),
  ],
}
