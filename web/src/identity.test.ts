import {
  IDENTITY_KEY,
  clearIdentity,
  loadIdentity,
  normalizeNetId,
  saveIdentity,
} from './identity'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('normalizeNetId', () => {
  it.each([
    ['ABC123', 'abc123'],
    ['abc123@rutgers.edu', 'abc123'],
    [' jd12 ', 'jd12'],
    ['jd', 'jd'],
  ])('%j -> %j', (raw, expected) => {
    expect(normalizeNetId(raw)).toBe(expected)
  })

  it.each(['a', '123', 'ab-12', 'ab12cd', '', 'a'.repeat(21), '@rutgers.edu'])(
    'rejects %j',
    (raw) => {
      expect(normalizeNetId(raw)).toBeNull()
    },
  )
})

describe('storage', () => {
  it('round-trips an identity', () => {
    saveIdentity({ netid: 'abc123', mode: 'text' })
    expect(loadIdentity()).toEqual({ netid: 'abc123', mode: 'text' })
    clearIdentity()
    expect(loadIdentity()).toBeNull()
  })

  it('ignores a stored value of the wrong shape', () => {
    localStorage.setItem(IDENTITY_KEY, '{"netid":"ab-12","mode":"voice"}')
    expect(loadIdentity()).toBeNull()
    localStorage.setItem(IDENTITY_KEY, 'not json')
    expect(loadIdentity()).toBeNull()
  })

  it('tolerates storage that throws', () => {
    const boom = () => {
      throw new Error('SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(boom)
    expect(() => saveIdentity({ netid: 'abc123', mode: 'voice' })).not.toThrow()
    expect(loadIdentity()).toBeNull()
    expect(() => clearIdentity()).not.toThrow()
  })
})
