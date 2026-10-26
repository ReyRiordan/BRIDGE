/**
 * Typed copies of what `GET /scenario/{id}` returns for
 * resources/scenarios/scenario_1 and scenario_3 (trimmed). Kept in sync by
 * hand; the layer values here are what the ordering tests assert against.
 */
import type { Scenario } from '../../types/scenario'

export const scenarioFixture: Scenario = {
  id: 'scenario_1',
  title: 'Emergency Department: Agitated Patient with Autism',
  intro_image: 'intro.jpg',
  environment: null,
  patient_frame_ext: 'png',
  inactivity: null,
  intro:
    'A 22-year-old male with autism spectrum disorder presents with abdominal pain.',
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

export const WARD_KEY = 'Acknowledge autonomy and outside pressures'
export const WARD_BARRIER = 'Explore the specific barrier'
export const WARD_END = 'Security or physical prevention'
export const WARD_DEBRIEF =
  'A patient with decision-making capacity has the right to leave.'

/**
 * scenario_3, trimmed to the actions that exercise every new mechanic: the
 * environment plate, webp frames from a start of 6, an inactive-only
 * persisting layer (staff, gone once the KEY is taken), a conditional action
 * whose active layer must sort BELOW the staff (layer −2), and an END action.
 */
export const wardFixture: Scenario = {
  id: 'scenario_3',
  title: 'Inpatient Ward: Patient with a GI Bleed Who Wants to Leave',
  intro_image: 'intro.webp',
  environment: 'environment.webp',
  patient_frame_ext: 'webp',
  inactivity: { seconds: 120, point_change: 1 },
  intro: 'A 47-year-old man admitted overnight for a suspected GI bleed.',
  goal: 'Work toward a plan that protects his safety while respecting his autonomy.',
  time_limit: 300,
  point_bar: { max: 10, start: 6, goal: 0 },
  actions: [
    {
      type: WARD_KEY,
      desc: 'Recognize outside responsibilities; the decision is his',
      point_change: -3,
      inactive: 'staff_present.webp',
      active: null,
      persist: true,
      layer: -1,
    },
    {
      type: WARD_BARRIER,
      desc: 'Ask what is waiting for him at work',
      point_change: -2,
      requires: WARD_KEY,
      point_change_without: -1,
      inactive: null,
      active: 'door_closed.webp',
      persist: true,
      layer: -2,
    },
    {
      type: 'Offer control',
      desc: 'Give reasonable choices',
      point_change: -1,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: 'Tell the patient he cannot leave',
      desc: 'E.g. “You’re not allowed to leave”',
      point_change: 3,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
    {
      type: WARD_END,
      desc: 'Call security, block the doorway, or attempt restraint',
      point_change: 0,
      ends_simulation: true,
      debrief: WARD_DEBRIEF,
      inactive: null,
      active: null,
      persist: false,
      layer: null,
    },
  ],
}
