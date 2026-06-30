import { useMemo, useState } from 'react'
import './App.css'
import { useEventStream } from './useEventStream'
import { groupIncidents } from './groupIncidents'

function ConnectionDot({ connected }) {
  return (
    <span className={`status-dot ${connected ? 'connected' : 'disconnected'}`}>
      {connected ? 'connected' : 'disconnected'}
    </span>
  )
}

function ActivityColumn({ events }) {
  const reversed = useMemo(() => [...events].reverse(), [events])
  return (
    <div className="column">
      <h2>Target App</h2>
      <div className="column-body">
        {reversed.length === 0 && <p className="empty">No activity yet.</p>}
        {reversed.map((e) => (
          <div key={e.id} className={`activity-row ${e.level === 'error' ? 'level-error' : 'level-info'}`}>
            <span className="activity-dot" />
            <span className="activity-name">{e.name}</span>
            <span className="activity-time">{new Date(e.timestamp * 1000).toLocaleTimeString()}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function detectorStatus(incident) {
  if (incident.requires_ai) return { text: 'sent to AI', cls: 'ok' }
  if (incident.ai_worthy && incident.status !== 'detected') return { text: 'AI-worthy', cls: 'ok' }
  if (incident.ai_worthy) return { text: 'AI-worthy, not dispatched', cls: 'warn' }
  return { text: 'not AI-worthy', cls: 'muted' }
}

function DetectorColumn({ incidents }) {
  return (
    <div className="column">
      <h2>Detector</h2>
      <div className="column-body">
        {incidents.length === 0 && <p className="empty">No incidents yet.</p>}
        {incidents.map((incident) => {
          const status = detectorStatus(incident)
          return (
            <div key={incident.id} className="card">
              <div className="card-title">{incident.incident_event}</div>
              <div className="card-meta">
                <span className={`pill severity-${incident.severity}`}>{incident.severity}</span>
                <span className={`pill status-${status.cls}`}>{status.text}</span>
              </div>
              {incident.pattern && <div className="card-note">cascade: {incident.pattern}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ExpandableText({ text }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="expandable">
      <button className="expand-toggle" onClick={() => setOpen(!open)}>
        {open ? 'hide' : 'show'} output
      </button>
      {open && <pre className="expand-content">{text}</pre>}
    </div>
  )
}

function InvestigatorColumn({ incidents }) {
  const active = incidents.filter((i) => i.investigatorEvents.length > 0)
  return (
    <div className="column">
      <h2>Investigator (AI 1)</h2>
      <div className="column-body">
        {active.length === 0 && <p className="empty">No investigations yet.</p>}
        {active.map((incident) => (
          <div key={incident.id} className="card">
            <div className="card-title">{incident.incident_event}</div>
            {incident.investigatorEvents.map((e, i) => {
              if (e.type === 'ai_analysis_started') {
                return <div key={i} className="step">started investigating...</div>
              }
              if (e.type === 'tool_call') {
                return (
                  <div key={i} className="step">
                    <span className="step-tool">{e.tool}</span>({e.input || ''})
                    <ExpandableText text={e.output} />
                  </div>
                )
              }
              if (e.type === 'stage_complete') {
                return (
                  <div key={i} className="step step-final">
                    <strong>Root cause</strong>
                    <pre className="result-text">{e.output}</pre>
                  </div>
                )
              }
              return null
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function FixerColumn({ incidents }) {
  const active = incidents.filter((i) => i.fixerEvents.length > 0)
  return (
    <div className="column">
      <h2>Fixer (AI 2)</h2>
      <div className="column-body">
        {active.length === 0 && <p className="empty">No fixes proposed yet.</p>}
        {active.map((incident) => (
          <div key={incident.id} className="card">
            <div className="card-title">{incident.incident_event}</div>
            {incident.fixerEvents.map((e, i) => (
              <pre key={i} className="result-text">{e.output || e.error}</pre>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function App() {
  const activity = useEventStream('/api/events/activity/history', '/api/events/activity/stream')
  const pipeline = useEventStream('/api/events/pipeline/history', '/api/events/pipeline/stream')
  const incidents = useMemo(() => groupIncidents(pipeline.events), [pipeline.events])

  return (
    <div className="app">
      <header className="app-header">
        <h1>SentinelAI — Live</h1>
        <ConnectionDot connected={activity.connected && pipeline.connected} />
      </header>
      <main className="columns">
        <ActivityColumn events={activity.events} />
        <DetectorColumn incidents={incidents} />
        <InvestigatorColumn incidents={incidents} />
        <FixerColumn incidents={incidents} />
      </main>
    </div>
  )
}

export default App
