/**
 * A typed copy of what `GET /scenario` returns for resources/scenario_1.json.
 * Kept in sync by hand; the layer values here are what the ordering tests
 * assert against.
 */
import type { Scenario } from '../../types/scenario'

export const scenarioFixture: Scenario = {
  intro: 'A 22-year-old male with autism spectrum disorder presents with abdominal pain.',
  goal: 'Reduce the patient’s agitation using appropriate de-escalation strategies.',
  time_limit: 300,
  point_bar: { max: 10, start: 5, goal: 0 },
  actions: [
    {
      type: 'Caregiver involvement',
      desc: 'Ask caregiver for guidance or involve them in calming',
      point_change: -3,
      inactive: 'caregiver_default.png',
      active: 'caregiver_active.png',
      persist: true,
      layer: 1,
    },
    {
      type: 'Environmental',
      desc: 'Dim lights, reduce noise, limit staff',
      point_change: -2,
      inactive: 'environment_default.png',
      active: 'environment_active.png',
      persist: true,
      layer: -1,
    },
    {
      type: 'Verbal Communication',
      desc: 'Calm tone, simple explanations, reassurance',
      point_change: -1,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Offer Control',
      desc: 'Give choices (explain vs show)',
      point_change: -1,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Acknowledge distress',
      desc: 'E.g. “I see this is overwhelming”',
      point_change: -1,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Delay Procedure',
      desc: 'Acknowledge that IV will be done later',
      point_change: -1,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Force IV',
      desc: 'Attempt IV while agitated',
      point_change: 4,
      inactive: null,
      active: 'iv_active.png',
      persist: false,
      layer: 1,
    },
    {
      type: 'Authoritative tone',
      desc: 'E.g. “We need to do this now”',
      point_change: 2,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Restraint',
      desc: 'Chemical Sedations or Physical Restraints',
      point_change: 10,
      inactive: null,
      active: 'restraint_active.png',
      persist: true,
      layer: 1,
    },
  ],
}

/** The "zero code edits" probe: same scenario, different starting escalation. */
export function withPointBarStart(start: number): Scenario {
  return {
    ...scenarioFixture,
    point_bar: { ...scenarioFixture.point_bar, start },
  }
}
