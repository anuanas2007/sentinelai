import { useEffect, useMemo, useState } from 'react'
import './App.css'
import { useEventStream } from './useEventStream'
import { groupIncidents } from './groupIncidents'
import { Modal } from './Modal'
import { AiOutput } from './AiOutput'
import { RatingButtons } from './RatingButtons'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:9000'
const GRAFANA_URL = 'http://localhost:3000/d/sentinelai-pipeline?orgId=1&kiosk'
const STORE_URL = 'http://localhost:5174'

const SCENARIOS = [
  {
    id: 'analytics_crash',
    label: 'Analytics crash',
    desc: '30% crash rate per call',
    explanation: 'Hits /analytics multiple times concurrently. Each call has a 30% chance of crashing with a division-by-zero (a simulated bug where active_users is always 0). With 10 calls, around 3 will fail with analytics_failed. This is a threshold-based error -- the detector needs 3 within a window before escalating to AI.',
    params: [{ key: 'calls', label: 'Number of concurrent calls', default: 10, min: 1, max: 50 }],
  },
  {
    id: 'external_timeout',
    label: 'External timeout',
    desc: 'Guaranteed timeout on every call',
    explanation: 'Hits /external multiple times. Each call tries to reach httpbin.org/delay/5 with a 3-second timeout -- guaranteed to fail every time. Simulates an unreliable third-party dependency. Threshold-based, needs 3 failures in a window.',
    params: [{ key: 'calls', label: 'Number of concurrent calls', default: 5, min: 1, max: 20 }],
  },
  {
    id: 'user_not_found',
    label: 'User not found',
    desc: 'Immediate escalation, no threshold',
    explanation: 'Fetches a non-existent user ID from /users/{id}. Unlike threshold-based errors, user_not_found is an immediate classifier -- a single occurrence escalates straight to the AI investigator with no pattern needed. Use a user ID that does not exist in the database (anything above 5).',
    params: [
      { key: 'user_id', label: 'User ID (must not exist)', default: 9999, min: 100, max: 99999 },
      { key: 'calls', label: 'Number of calls', default: 1, min: 1, max: 5 },
    ],
  },
  {
    id: 'negative_balance',
    label: 'Negative balance',
    desc: 'Race condition on store credits',
    explanation: 'Sends many concurrent orders for the same user (Wireless Headphones, $89.99 each, paid with store credits). The credits check and deduction are not atomic -- multiple orders pass the check simultaneously, overdrawing the balance. Requires headphones to have stock > 0 (use "Add stock" first if needed) and the user to have sufficient credits (use "Add credits" first if needed).',
    params: [
      { key: 'user_id', label: 'User ID (1=Alice $800, 3=Charlie $400, 4=Diana $600)', default: 1, min: 1, max: 5 },
      { key: 'concurrent', label: 'Concurrent orders', default: 30, min: 5, max: 100 },
    ],
  },
  {
    id: 'admin_topup',
    label: 'Add credits',
    desc: 'Top up a user\'s store credits',
    explanation: 'Directly adds store credits to a user account. Use this to reset a user after the negative balance scenario has drained them.',
    params: [
      { key: 'user_id', label: 'User ID (1–5)', default: 1, min: 1, max: 5 },
      { key: 'top_up', label: 'Amount to add ($)', default: 500, min: 1, max: 9999 },
    ],
  },
  {
    id: 'admin_restock',
    label: 'Add stock',
    desc: 'Restock an item',
    explanation: 'Directly adds stock to an item. Use this to reset inventory after order scenarios have depleted it.',
    params: [
      { key: 'item_name', label: 'Item', type: 'select', default: 'headphones', options: ['headphones', 'keyboard', 'usb_hub', 'webcam', 'mouse_pad', 'desk_lamp', 'ssd', 'monitor', 'laptop_stand', 'wireless_charger', 'microphone', 'led_strip', 'controller', 'cable_pack', 'headphone_stand'] },
      { key: 'quantity', label: 'Quantity to add', default: 100, min: 1, max: 9999 },
    ],
  },
  {
    id: 'payment_cascade',
    label: 'Payment cascade',
    desc: 'Mixed order failures, cascade pattern',
    explanation: 'Sends concurrent orders using a mix of users and items. Orders for Bob (no balance) fail with order_failed_insufficient_balance; orders for item_b (no stock) fail with order_failed_insufficient_stock. These two error types alternate in quick succession, confirming a cascade pattern after 3 co-occurrences. The AI then determines whether they are causally related or independent failures.',
    params: [{ key: 'concurrent', label: 'Concurrent orders', default: 15, min: 5, max: 150 }],
  },
]

function ScenarioModal({ scenario, onClose }) {
  const [params, setParams] = useState(
    Object.fromEntries(scenario.params.map((p) => [p.key, p.default]))
  )
  const [fired, setFired] = useState(false)

  async function fire() {
    try {
      await fetch(`${API_BASE}/api/trigger/${scenario.id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      setFired(true)
      setTimeout(onClose, 1200)
    } catch {}
  }

  return (
    <Modal title={scenario.label} onClose={onClose}>
      <p className="scenario-explanation">{scenario.explanation}</p>
      <div className="scenario-params">
        {scenario.params.map((p) => (
          <div key={p.key} className="scenario-param">
            <label className="scenario-param-label">{p.label}</label>
            {p.type === 'select' ? (
              <select
                className="scenario-param-input"
                value={params[p.key]}
                onChange={(e) => setParams({ ...params, [p.key]: e.target.value })}
              >
                {p.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
              </select>
            ) : (
              <input
                className="scenario-param-input"
                type="number"
                min={p.min}
                max={p.max}
                value={params[p.key]}
                onChange={(e) => setParams({ ...params, [p.key]: Number(e.target.value) })}
              />
            )}
          </div>
        ))}
      </div>
      {fired ? (
        <div className="scenario-fired">Triggered -- watch the Target App column.</div>
      ) : (
        <button className="scenario-fire-btn" onClick={fire}>Fire</button>
      )}
    </Modal>
  )
}

function TriggerPanel() {
  const [activeScenario, setActiveScenario] = useState(null)
  const [trafficRunning, setTrafficRunning] = useState(false)

  useEffect(() => {
    fetch(`${API_BASE}/api/traffic/status`)
      .then((r) => r.json())
      .then((d) => setTrafficRunning(d.running))
      .catch(() => {})
  }, [])

  async function toggleTraffic() {
    const url = `${API_BASE}${trafficRunning ? '/api/traffic/stop' : '/api/traffic/start'}`
    try {
      const res = await fetch(url, { method: 'POST' })
      const data = await res.json()
      setTrafficRunning(data.running)
    } catch {}
  }

  return (
    <>
      <div className="trigger-panel">
        <span className="trigger-panel-label">Trigger</span>
        <div className="trigger-scenarios">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              className="trigger-btn"
              onClick={() => setActiveScenario(s)}
            >
              <span className="trigger-label">{s.label}</span>
              <span className="trigger-desc">{s.desc}</span>
            </button>
          ))}
        </div>
        <button
          className={`traffic-toggle${trafficRunning ? ' traffic-on' : ''}`}
          onClick={toggleTraffic}
        >
          <span className="traffic-dot" />
          {trafficRunning ? 'Stop traffic' : 'Start traffic'}
        </button>
      </div>
      {activeScenario && (
        <ScenarioModal scenario={activeScenario} onClose={() => setActiveScenario(null)} />
      )}
    </>
  )
}

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

  // fixer -- fixerEvents can now also contain a get_similar_incidents
  // tool_call (the tool moved here from the investigator), so the
  // actual fix text has to be found specifically, not assumed to be
  // whichever event arrived first.
  const toolCalls = incident.fixerEvents.filter((e) => e.type === 'tool_call')
  const fix = incident.fixerEvents.find((e) => e.type === 'stage_complete' || e.type === 'ai_analysis_failed')
  return (
    <Modal title={`Fix proposal: ${incident.incident_event}`} onClose={onClose}>
      {toolCalls.map((e, i) => (
        <div key={i} className="modal-tool-call">
          <strong>{e.tool}</strong>({e.input || ''})
          {e.tool === 'get_similar_incidents' ? (
            <AiOutput text={e.output} />
          ) : (
            <pre className="tool-output">{e.output}</pre>
          )}
        </div>
      ))}
      {fix && (
        <>
          <h4>Solution</h4>
          <AiOutput text={fix.output || fix.error} />
        </>
      )}
      <RatingButtons incidentId={incident.id} />
    </Modal>
  )
}

function App() {
  const activity = useEventStream('/api/events/activity/history', '/api/events/activity/stream')
  const pipeline = useEventStream('/api/events/pipeline/history', '/api/events/pipeline/stream')
  const incidents = useMemo(() => groupIncidents(pipeline.events), [pipeline.events])
  const [expanded, setExpanded] = useState(null) // { incident, view }
  const [tab, setTab] = useState('live') // 'live' | 'metrics' | 'store'

  return (
    <div className="app">
      <header className="app-header">
        <h1>SentinelAI</h1>
        <nav className="tab-nav">
          <button className={`tab-btn${tab === 'live' ? ' tab-active' : ''}`} onClick={() => setTab('live')}>Live</button>
          <button className={`tab-btn${tab === 'metrics' ? ' tab-active' : ''}`} onClick={() => setTab('metrics')}>Metrics</button>
          <button className="tab-btn" onClick={() => window.open(STORE_URL, '_blank')}>Store ↗</button>
        </nav>
        <ConnectionDot connected={activity.connected && pipeline.connected} />
      </header>

      {tab === 'live' && (
        <>
          <TriggerPanel />
          <main className="columns">
            <ActivityColumn events={activity.events} />
            <DetectorColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
            <InvestigatorColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
            <FixerColumn incidents={incidents} onExpand={(incident, view) => setExpanded({ incident, view })} />
          </main>
          {expanded && (
            <IncidentModal incident={expanded.incident} view={expanded.view} onClose={() => setExpanded(null)} />
          )}
        </>
      )}

      {tab === 'metrics' && (
        <iframe
          className="grafana-frame"
          src={GRAFANA_URL}
          title="SentinelAI Metrics"
        />
      )}
    </div>
  )
}

export default App
