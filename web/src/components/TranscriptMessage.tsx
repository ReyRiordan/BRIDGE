import type { TranscriptEntry } from '../state/useGame'

interface TranscriptMessageProps {
  entry: TranscriptEntry
}

function TranscriptMessage({ entry }: TranscriptMessageProps) {
  const isStudent = entry.role === 'student'
  return (
    <div
      data-role={entry.role}
      className={`flex flex-col gap-1 ${isStudent ? 'items-end' : 'items-start'}`}
    >
      <span className="px-1 text-[0.65rem] font-semibold tracking-[0.16em] text-ink-muted uppercase">
        {isStudent ? 'You' : 'Patient'}
      </span>
      <p
        className={`max-w-[85%] rounded-card px-4 py-2.5 text-sm leading-relaxed ${
          isStudent
            ? 'bg-student text-ink'
            : 'bg-patient text-ink ring-1 ring-edge'
        }`}
      >
        {entry.content}
      </p>
    </div>
  )
}

export default TranscriptMessage
