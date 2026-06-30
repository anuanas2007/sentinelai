import { useMemo, useState } from 'react'
import './App.css'
import { useEventStream } from './useEventStream'
import { groupIncidents } from './groupIncidents'
import { Modal } from './Modal'
import { AiOutput } from './AiOutput'
import { RatingButtons } from './RatingButtons'

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
  // Mirrors the same distinction fixed in log_collector.py's print
  // output: ai_worthy + not requires_ai can mean two genuinely
  // different things -- "hasn't crossed the incident threshold yet"
  // (severity stays "warning", AI was never even attempted) vs.
  // "crossed the threshold, but the 120s dispatch cooldown is still
  // active from a recent call" -- previously this UI conflated both
  // into one vague "not dispatched" label.
  if (!incident.ai_worthy) return { text: 'not AI-worthy', cls: 'muted' }
  if (incident.requires_ai) return { text: 'sent to AI', cls: 'ok' }
  if (incident.severity === 'warning') return { text: 'below threshold', cls: 'muted' }
  return { text: 'AI cooldown', cls: 'warn' }
}

function DetectorColumn({ incidents, onExpand }) {
  return (
    <div className="column">
      <h2>Detector</h2>
      <div className="column-body">
        {incidents.length === 0 && <p className="empty">No incidents yet.</p>}
        {incidents.map((incident) => {
          const status = detectorStatus(incident)
          return (
            <div key={incident.id} className="card card-clickable" onClick={() => onExpand(incident, 'detector')}>
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

function InvestigatorColumn({ incidents, onExpand }) {
  const active = incidents.filter((i) => i.investigatorEvents.length > 0)
  return (
    <div className="column">
      <h2>Investigator (AI 1)</h2>
      <div className="column-body">
        {active.length === 0 && <p className="empty">No investigations yet.</p>}
        {active.map((incident) => (
          <div key={incident.id} className="card card-clickable" onClick={() => onExpand(incident, 'investigator')}>
            <div className="card-title">{incident.incident_event}</div>
            <div className="card-note">
              {incident.investigatorEvents.filter((e) => e.type === 'tool_call').length} tool call(s)
              {incident.investigatorEvents.some((e) => e.type === 'stage_complete') ? ' · root cause found' : ' · investigating...'}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function FixerColumn({ incidents, onExpand }) {
  const active = incidents.filter((i) => i.fixerEvents.length > 0)
  return (
    <div className="column">
      <h2>Fixer (AI 2)</h2>
      <div className="column-body">
        {active.length === 0 && <p className="empty">No fixes proposed yet.</p>}
        {active.map((incident) => (
          <div key={incident.id} className="card card-clickable" onClick={() => onExpand(incident, 'fixer')}>
            <div className="card-title">{incident.incident_event}</div>
            <div className="card-note">click to view fix proposal</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function IncidentModal({ incident, view, onClose }) {
  if (view === 'detector') {
    return (
      <Modal title={`Detected: ${incident.incident_event}`} onClose={onClose}>
        <p><strong>Severity:</strong> {incident.severity}</p>
        <p><strong>Requires AI:</strong> {String(incident.requires_ai)}</p>
        <p><strong>AI-worthy:</strong> {String(incident.ai_worthy)}</p>
        {incident.pattern && <p><strong>Cascade pattern:</strong> {incident.pattern}</p>}
      </Modal>
    )
  }

  if (view === 'investigator') {
    const toolCalls = incident.investigatorEvents.filter((e) => e.type === 'tool_call')
    const rootCause = incident.investigatorEvents.find((e) => e.type === 'stage_complete')
    return (
      <Modal title={`Investigation: ${incident.incident_event}`} onClose={onClose}>
        {toolCalls.map((e, i) => (
          <div key={i} className="modal-tool-call">
            <strong>{e.tool}</strong>({e.input || ''})
            {/* Raw tool output (file contents, filenames, plain
                sentences we wrote ourselves) -- not markdown, so this
                stays a plain <pre>, not AiOutput. Markdown collapses
                newlines/indentation in plain text into one run-on
                paragraph, which is exactly wrong for source code. */}
            <pre className="tool-output">{e.output}</pre>
          </div>
        ))}
        {rootCause && (
          <>
            <h4>Root cause</h4>
            <AiOutput text={rootCause.output} />
          </>
        )}
      </Modal>
    )
  }

  // fixer
  const fix = incident.fixerEvents[0]
  return (
    <Modal title={`Fix proposal: ${incident.incident_event}`} onClose={onClose}>
      <AiOutput text={fix?.output || fix?.error} />
      <RatingButtons incidentId={incident.id} />
    </Modal>
  )
}

function App() {
  const activity = useEventStream('/api/events/activity/history', '/api/events/activity/stream')
  const pipeline = useEventStream('/api/events/pipeline/history', '/api/events/pipeline/stream')
  const incidents = useMemo(() => groupIncidents(pipeline.events), [pipeline.events])
  const [expanded, setExpanded] = useState(null) // { incident, view }

  return (
    <div className="app">
      <header className="app-header">
        <h1>SentinelAI — Live</h1>
        <ConnectionDot connected={activity.connected && pipeline.connected} />
      </header>
      <main className="columns">
        <ActivityColumn events={activity.events} />
        <DetectorColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
        <InvestigatorColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
        <FixerColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
      </main>
      {expanded && (
        <IncidentModal incident={expanded.incident} view={expanded.view} onClose={() => setExpanded(null)} />
      )}
    </div>
  )
}

export default App
