interface PatientMuteToggleProps {
  muted: boolean
  onToggle: () => void
}

/**
 * Text mode's control over the scene, in the corner Voice mode gives the mic
 * pill. The patient still speaks every reply; muting only silences playback
 * (for a quiet room), and the replies are in the transcript either way.
 */
function PatientMuteToggle({ muted, onToggle }: PatientMuteToggleProps) {
  return (
    <button
      type="button"
      aria-pressed={muted}
      aria-label="Mute patient audio"
      data-patient-audio={muted ? 'muted' : 'on'}
      onClick={onToggle}
      className={`absolute top-4 left-4 z-50 cursor-pointer rounded-full px-4 py-1.5 text-xs font-semibold tracking-wide backdrop-blur-sm transition focus-visible:ring-2 focus-visible:ring-accent focus-visible:outline-none ${
        muted ? 'bg-canvas/70 text-ink-muted' : 'bg-surface-raised/85 text-ink'
      }`}
    >
      {muted ? '🔇 Patient muted' : '🔊 Patient audio on'}
    </button>
  )
}

export default PatientMuteToggle
