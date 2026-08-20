interface MicStatusProps {
  /** True while the remote (patient) audio is playing. */
  agentSpeaking: boolean
  /** The mic's actual track state — the anti-echo auto-mute drives it. */
  muted: boolean
}

/**
 * Read-only mic pill. The hook mutes the student's track while the patient
 * speaks (otherwise the speaker output re-triggers the server-side VAD), which
 * is otherwise invisible; this makes it legible. Deliberately not a toggle —
 * a manual control just invites fighting the auto-mute.
 *
 * No `role="status"`: the action badge owns the live region on this stage, and
 * two of them would fight for the screen reader's attention.
 */
function MicStatus({ agentSpeaking, muted }: MicStatusProps) {
  const off = agentSpeaking || muted
  return (
    <div
      aria-label="Microphone status"
      data-mic={off ? 'muted' : 'listening'}
      className={`absolute top-4 left-4 z-50 rounded-full px-4 py-1.5 text-xs font-semibold tracking-wide backdrop-blur-sm ${
        off ? 'bg-canvas/70 text-ink-muted' : 'bg-esc-calm/85 text-canvas'
      }`}
    >
      {off ? '🔇 Patient speaking' : '🎤 Listening'}
    </div>
  )
}

export default MicStatus
