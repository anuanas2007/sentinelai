import { useEffect, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:9000'

/**
 * Fetches history once, then appends new events from the matching SSE
 * endpoint as they arrive. Shared between the pipeline and activity
 * streams -- they differ only in which URLs they point at, not in how
 * fetch-then-stream-append works.
 */
export function useEventStream(historyPath, streamPath) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const lastIdRef = useRef(0)

  useEffect(() => {
    let cancelled = false

    fetch(`${API_BASE}${historyPath}`)
      .then((r) => r.json())
      .then((history) => {
        if (cancelled) return
        setEvents(history)
        if (history.length) lastIdRef.current = history[history.length - 1].id
      })
      .catch(() => {})

    const es = new EventSource(`${API_BASE}${streamPath}`)
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = (msg) => {
      const event = JSON.parse(msg.data)
      if (event.id <= lastIdRef.current) return
      lastIdRef.current = event.id
      setEvents((prev) => [...prev, event])
    }

    return () => {
      cancelled = true
      es.close()
    }
  }, [historyPath, streamPath])

  return { events, connected }
}
