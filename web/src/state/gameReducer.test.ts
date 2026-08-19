import { gameReducer, initialState, type GameState } from './gameState'
import { toViewModel } from './selectors'
import {
  scenarioFixture,
  withPointBarStart,
} from './__fixtures__/scenario.fixture'
import {
  failRun,
  foldTo,
  noisyEvents,
  successRun,
} from './__fixtures__/goldenRun'

const loaded = (): GameState =>
  gameReducer(initialState, {
    type: 'SCENARIO_LOADED',
    scenario: scenarioFixture,
  })

describe('control transitions', () => {
  it('SCENARIO_LOADED derives the whole starting frame from the payload', () => {
    const state = loaded()
    expect(state.phase).toBe('intro')
    expect(state.escalation).toBe(5)
    expect(state.max).toBe(10)
    expect(state.timer).toEqual({ elapsed: 0, limit: 300 })
    expect(state.scenario).toBe(scenarioFixture)
  })

  it('BEGIN only fires from intro', () => {
    expect(gameReducer(loaded(), { type: 'BEGIN' }).phase).toBe('game')
    const untouched = initialState
    expect(gameReducer(untouched, { type: 'BEGIN' })).toBe(untouched)
  })

  it('SHOW_END requires a recorded game_over', () => {
    const playing = gameReducer(loaded(), { type: 'BEGIN' })
    expect(gameReducer(playing, { type: 'SHOW_END' })).toBe(playing)

    const over = foldTo(successRun)
    expect(gameReducer(over, { type: 'SHOW_END' }).phase).toBe('end')
  })

  it('PLAY_AGAIN drops the cached scenario so Start refetches it', () => {
    const reset = gameReducer(foldTo(successRun), { type: 'PLAY_AGAIN' })
    expect(reset.phase).toBe('start')
    expect(reset.scenario).toBeNull()
    expect(reset.transcript).toEqual([])
    expect(reset.actionsTaken.size).toBe(0)
    expect(reset.gameOver).toBeNull()
    // A fresh Set, not the module-level one shared with initialState.
    expect(reset.actionsTaken).not.toBe(initialState.actionsTaken)
  })
})

describe('wire events', () => {
  it('records game_over once and is idempotent afterwards', () => {
    const over = foldTo(failRun)
    const again = gameReducer(over, {
      v: 1,
      type: 'game_over',
      status: 'success',
      reason: 'a contradicting late duplicate',
    })
    expect(again).toBe(over)
    expect(again.gameOver).toEqual({
      status: 'fail',
      reason: 'The patient was restrained.',
    })
  })

  it('ignores unknown types and non-v1 envelopes, returning the identical state', () => {
    const playing = gameReducer(loaded(), { type: 'BEGIN' })
    for (const event of noisyEvents) {
      expect(gameReducer(playing, event)).toBe(playing)
    }
  })
})

describe('scenario-derived UI', () => {
  // THIS is the "zero code edits" acceptance criterion: nothing but the
  // scenario payload changes, and the whole opening frame moves with it.
  it('follows point_bar.start with no code changes', () => {
    const state = gameReducer(initialState, {
      type: 'SCENARIO_LOADED',
      scenario: withPointBarStart(7),
    })
    const vm = toViewModel(state)
    expect(vm.escalation.value).toBe(7)
    expect(vm.escalation.pct).toBe(70)
    expect(vm.layers.find((l) => l.key === '__patient__')?.src).toBe(
      '/visuals/patient_7.png',
    )
  })
})
