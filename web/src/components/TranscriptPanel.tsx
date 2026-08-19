import { useEffect, useRef } from 'react'
import TranscriptMessage from './TranscriptMessage'
import WaitingIndicator from './WaitingIndicator'
import type { TranscriptEntry } from '../state/useGame'

interface TranscriptPanelProps {
  transcript: TranscriptEntry[]
  awaitingPatient: boolean
}

function TranscriptPanel({
  transcript,
  awaitingPatient,
}: TranscriptPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keyed on the message count, not the array identity: a re-render that did
  // not add a turn should not yank the panel away from a user who scrolled up.
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [transcript.length])

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-card border border-edge bg-surface">
      <h2 className="border-b border-edge px-5 py-3 text-xs font-semibold tracking-[0.18em] text-ink-muted uppercase">
        Conversation Transcript
      </h2>
      <div
        ref={scrollRef}
        className="flex flex-1 flex-col gap-4 overflow-y-auto p-5"
      >
        {transcript.length === 0 && !awaitingPatient && (
          <p className="text-sm text-ink-muted italic">
            Start speaking to begin the conversation.
          </p>
        )}
        {transcript.map((entry) => (
          <TranscriptMessage key={entry.id} entry={entry} />
        ))}
        {awaitingPatient && <WaitingIndicator />}
      </div>
    </section>
  )
}

export default TranscriptPanel
