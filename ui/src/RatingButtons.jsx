import { useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:9000'

const OPTIONS = [
  { value: 'correct', label: '✓ Correct' },
  { value: 'partial', label: '~ Partial' },
  { value: 'incorrect', label: '✗ Incorrect' },
]

/**
 * This isn't just UI polish -- a rating recorded here is the actual
 * outcome-tracking data the learned classifier and fix-accuracy
 * benchmarking have both been blocked on, captured as a side effect of
 * normal review instead of a separate labeling chore.
 */
export function RatingButtons({ incidentId }) {
  const [submitted, setSubmitted] = useState(null)
  const [error, setError] = useState(null)

  async function submit(rating) {
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/api/incidents/${incidentId}/rate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating }),
      })
      const body = await res.json()
      if (body.status !== 'ok') {
        setError(body.status === 'not_found' ? 'Not stored yet — try again shortly.' : 'Failed to save.')
        return
      }
      setSubmitted(rating)
    } catch {
      setError('Failed to save.')
    }
  }

  if (submitted) {
    return <div className="rating-confirmed">Rated: {OPTIONS.find((o) => o.value === submitted)?.label}</div>
  }

  return (
    <div className="rating-buttons">
      <span className="rating-label">Was this fix correct?</span>
      <div className="rating-options">
        {OPTIONS.map((o) => (
          <button key={o.value} className={`rating-btn rating-${o.value}`} onClick={() => submit(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
      {error && <div className="rating-error">{error}</div>}
    </div>
  )
}
