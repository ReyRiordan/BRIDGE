/**
 * The handler is deliberately thin: it dispatches whatever the channel parsed
 * to and lets the reducer decide what is acceptable. These tests pin that —
 * every wire event goes through untouched, and only non-objects are dropped.
 */

import { expect, test, vi } from 'vitest'
import { createGameEventHandler } from './gameEvents'

test('dispatches a parsed game event unchanged', () => {
  const dispatch = vi.fn()
  const event = {
    v: 1,
    type: 'state_update',
    escalation: 6,
    max: 10,
    active_actions: [],
    status: 'agitated',
  }

  createGameEventHandler(dispatch)(event)

  expect(dispatch).toHaveBeenCalledWith(event)
})

test('forwards unknown and future-version envelopes — the reducer drops them', () => {
  const dispatch = vi.fn()
  const handler = createGameEventHandler(dispatch)

  handler({ v: 1, type: 'not_a_real_event' })
  handler({ v: 2, type: 'state_update' })

  expect(dispatch).toHaveBeenCalledTimes(2)
})

test('ignores non-objects', () => {
  const dispatch = vi.fn()
  const handler = createGameEventHandler(dispatch)

  handler(null)
  handler('state_update')
  handler(42)
  handler([{ v: 1, type: 'timer' }])

  expect(dispatch).not.toHaveBeenCalled()
})
