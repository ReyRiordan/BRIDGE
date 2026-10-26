import { useEffect } from 'react'
import ActionBadge from './ActionBadge'
import MicStatus from './MicStatus'
import SceneLayer from './SceneLayer'
import TimerPill from './TimerPill'
import {
  patientFrameSources,
  type ClockView,
  type LastAction,
  type SceneLayerView,
} from '../state/useGame'
import type { Scenario } from '../types/scenario'

interface SceneStageProps {
  /** Null only before a scenario loads; nothing is preloaded then. */
  scenario: Scenario | null
  layers: SceneLayerView[]
  clock: ClockView
  badge: LastAction | null
  /** Mic pill state — the auto-mute is otherwise invisible to the student. */
  mic: { agentSpeaking: boolean; muted: boolean }
}

/**
 * The composited scene.
 *
 * The stage is a fixed-aspect box (`aspect-stage` = the art's native 1196×880).
 * The legacy UI sized each layer independently, so they drifted apart as the
 * column resized; pinning one aspect on the container and absolutely filling it
 * with every layer makes drift impossible.
 */
function SceneStage({ scenario, layers, clock, badge, mic }: SceneStageProps) {
  // Warm every patient frame once per scenario, so escalation changes
  // cross-fade instead of popping in after a network round trip mid-turn.
  useEffect(() => {
    if (scenario === null) return
    for (const src of patientFrameSources(scenario)) {
      const img = new Image()
      img.src = src
    }
  }, [scenario])

  return (
    <div className="relative aspect-stage w-full max-w-4xl overflow-hidden rounded-card border border-edge bg-canvas shadow-2xl">
      {layers.map((layer) => (
        <SceneLayer
          key={layer.key}
          layerKey={layer.key}
          src={layer.src}
          z={layer.z}
        />
      ))}

      <TimerPill text={clock.text} urgent={clock.urgent} />
      <MicStatus agentSpeaking={mic.agentSpeaking} muted={mic.muted} />

      {badge && (
        // Keyed by id: back-to-back detections remount and re-pop.
        <ActionBadge
          key={badge.id}
          actionType={badge.actionType}
          desc={badge.desc}
          pointChange={badge.pointChange}
        />
      )}
    </div>
  )
}

export default SceneStage
