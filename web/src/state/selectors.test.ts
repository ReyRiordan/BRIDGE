import { gameReducer, initialState, type GameState } from './gameState'
import {
  patientSrc,
  selectAwaitingPatient,
  selectChecklist,
  selectClock,
  selectEscalation,
  selectLayers,
} from './selectors'
import { scenarioFixture } from './__fixtures__/scenario.fixture'

const base = (overrides: Partial<GameState> = {}): GameState => ({
  ...gameReducer(initialState, {
    type: 'SCENARIO_LOADED',
    scenario: scenarioFixture,
  }),
  ...overrides,
})

describe('patientSrc', () => {
  it('clamps to the frames that exist and rounds to the nearest', () => {
    expect(patientSrc(-3)).toBe('/visuals/patient_0.png')
    expect(patientSrc(99)).toBe('/visuals/patient_10.png')
    expect(patientSrc(3.4)).toBe('/visuals/patient_3.png')
    expect(patientSrc(3.6)).toBe('/visuals/patient_4.png')
  })
})

describe('selectEscalation', () => {
  it('treats low as good and switches tone at 30/60/80 %', () => {
    const tone = (escalation: number) =>
      selectEscalation(base({ escalation, max: 10 })).tone
    expect(tone(2.9)).toBe('calm')
    expect(tone(3)).toBe('watch') // exactly 30 % is no longer calm
    expect(tone(5.9)).toBe('watch')
    expect(tone(6)).toBe('warn') // exactly 60 %
    expect(tone(7.9)).toBe('warn')
    expect(tone(8)).toBe('crit') // exactly 80 %
    expect(tone(10)).toBe('crit')
  })

  it('does not divide by a zero max', () => {
    expect(selectEscalation(base({ escalation: 4, max: 0 })).pct).toBe(0)
  })
})

describe('selectClock', () => {
  it('formats m:ss with unpadded minutes and flags the last 30 s', () => {
    const at = (elapsed: number, limit = 300) =>
      selectClock(base({ timer: { elapsed, limit } }))
    expect(at(0).text).toBe('5:00')
    expect(at(235).text).toBe('1:05')
    expect(at(291).text).toBe('0:09')
    expect(at(270).urgent).toBe(false) // exactly 30 s left is not urgent yet
    expect(at(271).urgent).toBe(true)
    // The runtime is authoritative for expiry; overrun just pins at zero.
    expect(at(400).text).toBe('0:00')
    expect(at(400).remaining).toBe(0)
  })
})

describe('selectLayers', () => {
  it('orders the composite by the scenario layer values', () => {
    expect(selectLayers(base()).map((l) => [l.key, l.layer, l.z])).toEqual([
      ['Environmental', -1, 1],
      ['__patient__', 0, 2],
      ['Caregiver involvement', 1, 3],
      ['Force IV', 1, 4],
      ['Restraint', 1, 5],
    ])
  })

  it('resolves each src from active_actions, hiding art-less inactive states', () => {
    const layers = selectLayers(
      base({ activeActions: ['Environmental', 'Force IV'] }),
    )
    const src = (key: string) => layers.find((l) => l.key === key)?.src
    expect(src('Environmental')).toBe('/visuals/environment_active.png')
    expect(src('Force IV')).toBe('/visuals/iv_active.png')
    expect(src('Caregiver involvement')).toBe('/visuals/caregiver_default.png')
    expect(src('Restraint')).toBeNull() // inactive: null
  })

  it('is empty-safe before the scenario loads', () => {
    expect(selectLayers(initialState).map((l) => l.key)).toEqual(['__patient__'])
  })
})

describe('selectChecklist', () => {
  it('splits taken actions by point_change sign and keeps misses', () => {
    const rows = selectChecklist(
      base({ actionsTaken: new Set(['Environmental', 'Restraint']) }),
    )
    const row = (type: string) => rows.find((r) => r.type === type)
    expect(row('Environmental')).toMatchObject({ status: 'found-good', delta: '-2' })
    expect(row('Restraint')).toMatchObject({ status: 'found-bad', delta: '+10' })
    expect(row('Offer Control')).toMatchObject({ status: 'missed', delta: '-1' })
    expect(rows).toHaveLength(scenarioFixture.actions.length)
  })
})

describe('selectAwaitingPatient', () => {
  it('is true exactly while the student spoke last', () => {
    expect(selectAwaitingPatient(base())).toBe(false)
    const withEntry = (role: 'student' | 'patient') =>
      base({ transcript: [{ id: 1, role, content: 'x', timestamp: 't' }] })
    expect(selectAwaitingPatient(withEntry('student'))).toBe(true)
    expect(selectAwaitingPatient(withEntry('patient'))).toBe(false)
  })
})
