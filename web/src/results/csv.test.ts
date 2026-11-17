import { csvCell, toCsv } from './csv'
import { summary } from './fixtures'

describe('csvCell', () => {
  it('quotes every value and doubles inner quotes', () => {
    expect(csvCell('plain')).toBe('"plain"')
    expect(csvCell(7)).toBe('"7"')
    expect(csvCell('say "hi"')).toBe('"say ""hi"""')
    expect(csvCell(undefined)).toBe('""')
    expect(csvCell(null)).toBe('""')
  })

  it('keeps commas and newlines inside the quotes', () => {
    expect(csvCell('a, b')).toBe('"a, b"')
    expect(csvCell('line 1\nline 2')).toBe('"line 1\nline 2"')
  })

  it.each(['=SUM(A1)', '+1', '-1+1', '@cmd', '\tx', '\rx'])(
    'guards the formula trigger in %j',
    (value) => expect(csvCell(value)).toBe(`"'${value}"`),
  )

  it('leaves a trigger in the middle alone', () => {
    expect(csvCell('a=b')).toBe('"a=b"')
  })
})

describe('toCsv', () => {
  it('writes a header, one CRLF row per session, and the session id', () => {
    const csv = toCsv([
      summary(),
      summary({
        session_id: 'scenario_1--b',
        netid: '=evil',
        mode: 'text',
        display_status: 'abandoned',
        end_reason: null,
        duration_s: undefined,
      }),
    ])
    const lines = csv.split('\r\n')
    expect(lines).toHaveLength(4) // header, 2 rows, trailing empty
    expect(lines[0]).toBe(
      '"Date","NetID","Scenario","Mode","Outcome","Start agitation","Final agitation","Time used (s)","Time limit (s)","Helpful","Escalating","Session ID"',
    )
    expect(lines[1]).toBe(
      '"2026-11-10T15:00:00+00:00","test01","Scenario One","voice","Success","5","0","212.4","300","3","1","scenario_1--a"',
    )
    expect(lines[2]).toContain(`"'=evil"`)
    expect(lines[2]).toContain('"Abandoned"')
    expect(lines[2]).toContain('"","300"')
    expect(lines[3]).toBe('')
  })
})
