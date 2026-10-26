import { gameReducer, initialState, type GameState } from './gameState'
import {
  ENVIRONMENT_KEY,
  PATIENT_KEY,
  patientFrameSources,
  patientSrc,
  selectAwaitingPatient,
  selectChecklist,
  selectClock,
  selectEscalation,
  selectLayers,
} from './selectors'
import {
  scenarioFixture,
  WARD_BARRIER,
  WARD_END,
  WARD_KEY,
  wardFixture,
} from './__fixtures__/scenario.fixture'
import type { Scenario } from '../types/scenario'

const base = (
  overrides: Partial<GameState> = {},
  scenario: Scenario = scenarioFixture,
): GameState => ({
  ...gameReducer(initialState, { type: 'SCENARIO_LOADED', scenario }),
  ...overrides,
})

const ward = (overrides: Partial<GameState> = {}) =>
  base(overrides, wardFixture)

describe('patientSrc', () => {
  it('clamps to the frames that exist and rounds to the nearest', () => {
    const src = (e: number) => patientSrc(scenarioFixture, e)
    expect(src(-3)).toBe('/visuals/scenario_1/patient_0.png')
    expect(src(99)).toBe('/visuals/scenario_1/patient_10.png')
    expect(src(3.4)).toBe('/visuals/scenario_1/patient_3.png')
    expect(src(3.6)).toBe('/visuals/scenario_1/patient_4.png')
  })

  it("uses the scenario's folder, frame extension and point-bar max", () => {
    expect(patientSrc(wardFixture, 6)).toBe(
      '/visuals/scenario_3/patient_6.webp',
    )
    const short = {
      ...wardFixture,
      point_bar: { ...wardFixture.point_bar, max: 4 },
    }
    expect(patientSrc(short, 9)).toBe('/visuals/scenario_3/patient_4.webp')
  })

  it('defaults the frame extension to png', () => {
    const noExt = { ...wardFixture, patient_frame_ext: undefined }
    expect(patientSrc(noExt, 1)).toBe('/visuals/scenario_3/patient_1.png')
  })
})

describe('patientFrameSources', () => {
  it('lists frames 0..point_bar.max for the scenario', () => {
    const frames = patientFrameSources(wardFixture)
    expect(frames).toHaveLength(11)
    expect(frames[0]).toBe('/visuals/scenario_3/patient_0.webp')
    expect(frames[10]).toBe('/visuals/scenario_3/patient_10.webp')
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
    expect(src('Environmental')).toBe(
      '/visuals/scenario_1/environment_active.png',
    )
    expect(src('Force IV')).toBe('/visuals/scenario_1/iv_active.png')
    expect(src('Caregiver involvement')).toBe(
      '/visuals/scenario_1/caregiver_default.png',
    )
    expect(src('Restraint')).toBeNull() // inactive: null
  })

  it('is empty-safe before the scenario loads', () => {
    expect(selectLayers(initialState)).toEqual([])
  })

  it('puts the environment plate under everything, then the staff, then the patient', () => {
    const layers = selectLayers(ward({ escalation: 6 }))
    expect(layers.map((l) => [l.key, l.z, l.src])).toEqual([
      [ENVIRONMENT_KEY, 1, '/visuals/scenario_3/environment.webp'],
      [WARD_BARRIER, 2, null],
      [WARD_KEY, 3, '/visuals/scenario_3/staff_present.webp'],
      [PATIENT_KEY, 4, '/visuals/scenario_3/patient_6.webp'],
    ])
  })

  it('stacks door_closed BELOW staff_present when the barrier comes first', () => {
    // Both visible: the barrier was explored before the KEY. In actions order
    // the door would land on top and erase the staff it repaints.
    const layers = selectLayers(ward({ activeActions: [WARD_BARRIER] }))
    const z = (key: string) => layers.find((l) => l.key === key)?.z ?? 0
    const src = (key: string) => layers.find((l) => l.key === key)?.src
    expect(src(WARD_BARRIER)).toBe('/visuals/scenario_3/door_closed.webp')
    expect(src(WARD_KEY)).toBe('/visuals/scenario_3/staff_present.webp')
    expect(z(WARD_BARRIER)).toBeLessThan(z(WARD_KEY))
    expect(z(WARD_KEY)).toBeLessThan(z(PATIENT_KEY))
  })

  it('removes the staff once the KEY action is active', () => {
    const layers = selectLayers(
      ward({ activeActions: [WARD_BARRIER, WARD_KEY] }),
    )
    const src = (key: string) => layers.find((l) => l.key === key)?.src
    expect(src(WARD_KEY)).toBeNull()
    expect(src(WARD_BARRIER)).toBe('/visuals/scenario_3/door_closed.webp')
  })
})

describe('selectChecklist', () => {
  it('splits taken actions by point_change sign and keeps misses', () => {
    const rows = selectChecklist(
      base({ actionsTaken: new Set(['Environmental', 'Restraint']) }),
    )
    const row = (type: string) => rows.find((r) => r.type === type)
    expect(row('Environmental')).toMatchObject({
      status: 'found-good',
      delta: '-2',
    })
    expect(row('Restraint')).toMatchObject({
      status: 'found-bad',
      delta: '+10',
    })
    expect(row('Offer Control')).toMatchObject({
      status: 'missed',
      delta: '-1',
    })
    expect(rows).toHaveLength(scenarioFixture.actions.length)
    expect(rows.every((r) => r.note === null)).toBe(true)
  })

  it('shows a conditional row at its with-KEY value plus a without note', () => {
    const row = selectChecklist(ward()).find((r) => r.type === WARD_BARRIER)
    expect(row).toMatchObject({
      delta: '-2',
      pointChange: -2,
      note: `(-1 without ${WARD_KEY})`,
      status: 'missed',
    })
  })

  it('renders an END action as END, bad once taken', () => {
    const missed = selectChecklist(ward()).find((r) => r.type === WARD_END)
    expect(missed).toMatchObject({ delta: 'END', status: 'missed', note: null })
    const taken = selectChecklist(
      ward({ actionsTaken: new Set([WARD_END]) }),
    ).find((r) => r.type === WARD_END)
    expect(taken).toMatchObject({ delta: 'END', status: 'found-bad' })
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
