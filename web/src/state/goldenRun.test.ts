/**
 * The acceptance test for [Rewrite F]: fold the hand-authored golden runs
 * through the reducer and assert the projected ViewModel — the bar, the scene
 * layers, the badge, the transcript, the clock and the end state — at every
 * milestone. No snapshots: the expected values are written out so a semantic
 * change has to be argued for in the diff, not blessed with `-u`.
 */
import { toViewModel, type ViewModel } from './selectors'
import { failRun, foldTo, successRun } from './__fixtures__/goldenRun'

/** The stable part of a frame, small enough to write out inline. */
function frame(vm: ViewModel) {
  return {
    esc: `${vm.escalation.value}/${vm.escalation.max}`,
    pct: vm.escalation.pct,
    tone: vm.escalation.tone,
    layers: vm.layers.map((l) => [l.key, l.z, l.src] as const),
    badge: vm.badge && [vm.badge.id, vm.badge.actionType, vm.badge.pointChange],
    clock: vm.clock.text,
    urgent: vm.clock.urgent,
    turns: vm.transcript.length,
    awaiting: vm.awaitingPatient,
    taken: vm.actionsTaken,
    status: vm.status,
    over: vm.gameOver,
  }
}

const ENV_OFF = '/visuals/environment_default.png'
const ENV_ON = '/visuals/environment_active.png'
const CG_OFF = '/visuals/caregiver_default.png'
const CG_ON = '/visuals/caregiver_active.png'
const IV_ON = '/visuals/iv_active.png'
const RESTRAINT_ON = '/visuals/restraint_active.png'

const layers = (
  env: string,
  patient: string,
  caregiver: string,
  iv: string | null,
  restraint: string | null,
) => [
  ['Environmental', 1, env],
  ['__patient__', 2, patient],
  ['Caregiver involvement', 3, caregiver],
  ['Force IV', 4, iv],
  ['Restraint', 5, restraint],
]

describe('golden success run', () => {
  it('reproduces the connect-time frame from the scenario alone', () => {
    expect(frame(toViewModel(foldTo(successRun, 1)))).toEqual({
      esc: '5/10',
      pct: 50,
      tone: 'watch',
      layers: layers(ENV_OFF, '/visuals/patient_5.png', CG_OFF, null, null),
      badge: null,
      clock: '5:00',
      urgent: false,
      turns: 0,
      awaiting: false,
      taken: [],
      status: 'agitated',
      over: null,
    })
  })

  it('lights the transient Force IV layer and goes critical', () => {
    expect(frame(toViewModel(foldTo(successRun, 5)))).toEqual({
      esc: '9/10',
      pct: 90,
      tone: 'crit',
      layers: layers(ENV_OFF, '/visuals/patient_9.png', CG_OFF, IV_ON, null),
      badge: [2, 'Force IV', 4],
      clock: '5:00',
      urgent: false,
      turns: 1,
      awaiting: true,
      taken: ['Force IV'],
      status: 'escalated',
      over: null,
    })
  })

  it('clears the transient layer, keeps the persistent one, and absorbs a mid-turn tick', () => {
    expect(frame(toViewModel(foldTo(successRun, 11)))).toEqual({
      esc: '5/10',
      pct: 50,
      tone: 'watch',
      // Force IV is gone from active_actions and has no inactive art → null.
      layers: layers(ENV_OFF, '/visuals/patient_5.png', CG_ON, null, null),
      // The second action of the turn owns the badge.
      badge: [6, 'Acknowledge distress', -1],
      clock: '4:48',
      urgent: false,
      turns: 3,
      awaiting: true,
      taken: ['Force IV', 'Caregiver involvement', 'Acknowledge distress'],
      status: 'settling',
      over: null,
    })
  })

  it('stacks the second persistent layer and drops into the calm tone', () => {
    expect(frame(toViewModel(foldTo(successRun, 19)))).toEqual({
      esc: '2/10',
      pct: 20,
      tone: 'calm',
      layers: layers(ENV_ON, '/visuals/patient_2.png', CG_ON, null, null),
      badge: [12, 'Environmental', -2],
      clock: '4:48',
      urgent: false,
      turns: 7,
      awaiting: true,
      taken: [
        'Force IV',
        'Caregiver involvement',
        'Acknowledge distress',
        'Verbal Communication',
        'Environmental',
      ],
      status: 'calmer',
      over: null,
    })
  })

  it('dedupes a repeated action but still advances the badge id', () => {
    const vm = frame(toViewModel(foldTo(successRun, 22)))
    // Verbal Communication was already taken at frame 19 — the set is unchanged…
    expect(vm.taken).toEqual([
      'Force IV',
      'Caregiver involvement',
      'Acknowledge distress',
      'Verbal Communication',
      'Environmental',
    ])
    // …but the badge is a new object with a new id, so it remounts and re-pops.
    expect(vm.badge).toEqual([15, 'Verbal Communication', -1])
  })

  it('ends at zero with game_over recorded before the closing patient line', () => {
    const vm = toViewModel(foldTo(successRun))
    expect(frame(vm)).toEqual({
      esc: '0/10',
      pct: 0,
      tone: 'calm',
      layers: layers(ENV_ON, '/visuals/patient_0.png', CG_ON, null, null),
      badge: [16, 'Offer Control', -1],
      clock: '4:48',
      urgent: false,
      turns: 10,
      // game_over does not stop the last patient line from landing.
      awaiting: false,
      taken: [
        'Force IV',
        'Caregiver involvement',
        'Acknowledge distress',
        'Verbal Communication',
        'Environmental',
        'Offer Control',
      ],
      status: 'calm',
      over: {
        status: 'success',
        reason: 'The patient is calm and ready to continue care.',
      },
    })
    // The phase is untouched: the 600 ms handoff lives in GameScreen.
    expect(vm.phase).toBe('game')
  })

  it('classifies every scenario action on the end-screen checklist', () => {
    expect(toViewModel(foldTo(successRun)).checklist).toEqual([
      {
        type: 'Caregiver involvement',
        desc: 'Ask caregiver for guidance or involve them in calming',
        pointChange: -3,
        delta: '-3',
        status: 'found-good',
      },
      {
        type: 'Environmental',
        desc: 'Dim lights, reduce noise, limit staff',
        pointChange: -2,
        delta: '-2',
        status: 'found-good',
      },
      {
        type: 'Verbal Communication',
        desc: 'Calm tone, simple explanations, reassurance',
        pointChange: -1,
        delta: '-1',
        status: 'found-good',
      },
      {
        type: 'Offer Control',
        desc: 'Give choices (explain vs show)',
        pointChange: -1,
        delta: '-1',
        status: 'found-good',
      },
      {
        type: 'Acknowledge distress',
        desc: 'E.g. “I see this is overwhelming”',
        pointChange: -1,
        delta: '-1',
        status: 'found-good',
      },
      {
        type: 'Delay Procedure',
        desc: 'Acknowledge that IV will be done later',
        pointChange: -1,
        delta: '-1',
        status: 'missed',
      },
      {
        type: 'Force IV',
        desc: 'Attempt IV while agitated',
        pointChange: 4,
        delta: '+4',
        status: 'found-bad',
      },
      {
        type: 'Authoritative tone',
        desc: 'E.g. “We need to do this now”',
        pointChange: 2,
        delta: '+2',
        status: 'missed',
      },
      {
        type: 'Restraint',
        desc: 'Chemical Sedations or Physical Restraints',
        pointChange: 10,
        delta: '+10',
        status: 'missed',
      },
    ])
  })
})

describe('golden fail run', () => {
  it('flags the clock as urgent inside the last 30 s', () => {
    const vm = toViewModel(foldTo(failRun, 2))
    expect(vm.clock.text).toBe('0:15')
    expect(vm.clock.urgent).toBe(true)
  })

  it('ends at max escalation with the restraint layer lit', () => {
    expect(frame(toViewModel(foldTo(failRun)))).toEqual({
      esc: '10/10',
      pct: 100,
      tone: 'crit',
      layers: layers(ENV_OFF, '/visuals/patient_10.png', CG_OFF, null, RESTRAINT_ON),
      badge: [5, 'Restraint', 10],
      clock: '0:15',
      urgent: true,
      turns: 4,
      awaiting: false,
      taken: ['Authoritative tone', 'Restraint'],
      status: 'peak',
      over: { status: 'fail', reason: 'The patient was restrained.' },
    })
  })
})

describe('per-frame invariants', () => {
  it.each([
    ['successRun', successRun],
    ['failRun', failRun],
  ])('%s holds them at every prefix', (_name, run) => {
    let previousBadgeId = 0
    for (let n = 0; n <= run.length; n++) {
      const vm = toViewModel(foldTo(run, n))

      // The composite never gains or loses a layer — only srcs change.
      expect(vm.layers).toHaveLength(5)
      expect(vm.layers.map((l) => l.z)).toEqual([1, 2, 3, 4, 5])

      expect(vm.escalation.pct).toBeGreaterThanOrEqual(0)
      expect(vm.escalation.pct).toBeLessThanOrEqual(100)

      const badgeId = vm.badge?.id ?? previousBadgeId
      expect(badgeId).toBeGreaterThanOrEqual(previousBadgeId)
      previousBadgeId = badgeId
    }
  })
})
