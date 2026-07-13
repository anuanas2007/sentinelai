import { useEffect, useState } from 'react'
import { API, AGENT_API, ITEM_EMOJI, fmt } from '../utils'

const SCENARIOS = [
  { id: 'negative_balance', label: 'Negative Balance Race', emoji: '💸', desc: 'Alice buys headphones with credits × 30 concurrent — overshoots balance', color: 'red' },
  { id: 'stock_race',       label: 'Stock Race',            emoji: '📦', desc: 'Keyboard reset to 3 stock, 50 concurrent orders — drives stock negative', color: 'orange' },
  { id: 'payment_cascade',  label: 'Payment Cascade',       emoji: '🌊', desc: 'Bob ($0) + webcam (no stock) orders interleaved — cascade pattern fires', color: 'purple' },
  { id: 'analytics_crash',  label: 'Analytics Crash',       emoji: '📊', desc: 'Division by zero in /analytics, 30% rate × 10 calls', color: 'yellow' },
  { id: 'external_timeout', label: 'External Timeout',      emoji: '⏱️', desc: '5s delay endpoint hit with 3s timeout — guaranteed failure × 5 calls', color: 'blue' },
  { id: 'user_not_found',   label: 'User Not Found',        emoji: '👤', desc: 'Single fetch of non-existent user — immediate escalation, no threshold', color: 'teal' },
]

function Section({ title, children }) {
  return (
    <section className="admin-section">
      <h2 className="admin-section-title">{title}</h2>
      {children}
    </section>
  )
}

function InventoryTable({ items, onRefresh }) {
  const [pending, setPending] = useState({})
  const [amounts, setAmounts] = useState({})

  async function restock(name) {
    const qty = parseInt(amounts[name] || 50, 10)
    setPending(p => ({ ...p, [name]: true }))
    await fetch(`${API}/admin/restock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_name: name, quantity: qty }),
    }).catch(() => {})
    setPending(p => ({ ...p, [name]: false }))
    onRefresh()
  }

  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead><tr><th>Item</th><th>Stock</th><th>Restock</th></tr></thead>
        <tbody>
          {items.map(item => (
            <tr key={item.name} className={item.stock <= 0 ? 'row-danger' : item.stock <= 3 ? 'row-warn' : ''}>
              <td className="admin-item-cell">
                <span className="admin-item-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</span>
                <span>{item.display_name}</span>
              </td>
              <td className={`admin-stock ${item.stock < 0 ? 'stock-negative' : item.stock === 0 ? 'stock-zero' : item.stock <= 3 ? 'stock-low' : ''}`}>
                {item.stock}
              </td>
              <td className="admin-action-cell">
                <input
                  className="admin-qty-input"
                  type="number"
                  min="1"
                  value={amounts[item.name] ?? 50}
                  onChange={e => setAmounts(a => ({ ...a, [item.name]: e.target.value }))}
                />
                <button
                  className="admin-action-btn"
                  disabled={pending[item.name]}
                  onClick={() => restock(item.name)}
                >
                  {pending[item.name] ? '...' : '+ Add'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UsersTable({ users, onRefresh }) {
  const [pending, setPending] = useState({})
  const [amounts, setAmounts] = useState({})

  async function topup(id) {
    const amount = parseFloat(amounts[id] || 200)
    setPending(p => ({ ...p, [id]: true }))
    await fetch(`${API}/admin/topup`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: id, amount }),
    }).catch(() => {})
    setPending(p => ({ ...p, [id]: false }))
    onRefresh()
  }

  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead><tr><th>User</th><th>Email</th><th>Credits</th><th>Top Up</th></tr></thead>
        <tbody>
          {users.map(u => (
            <tr key={u.id} className={u.balance < 0 ? 'row-danger' : ''}>
              <td className="admin-user-name">{u.name}</td>
              <td className="admin-muted">{u.email}</td>
              <td className={`admin-balance ${u.balance < 0 ? 'balance-negative' : ''}`}>{fmt(u.balance)}</td>
              <td className="admin-action-cell">
                <input
                  className="admin-qty-input"
                  type="number"
                  min="1"
                  value={amounts[u.id] ?? 200}
                  onChange={e => setAmounts(a => ({ ...a, [u.id]: e.target.value }))}
                />
                <button
                  className="admin-action-btn"
                  disabled={pending[u.id]}
                  onClick={() => topup(u.id)}
                >
                  {pending[u.id] ? '...' : '+ Add'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ScenarioCard({ s }) {
  const [state, setState] = useState('idle') // idle | firing | done | error

  async function fire() {
    setState('firing')
    try {
      const res = await fetch(`${AGENT_API}/api/trigger/${s.id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      setState(res.ok ? 'done' : 'error')
    } catch {
      setState('error')
    }
    setTimeout(() => setState('idle'), 3000)
  }

  return (
    <div className={`scenario-card scenario-${s.color}`}>
      <div className="scenario-card-top">
        <span className="scenario-card-emoji">{s.emoji}</span>
        <span className="scenario-card-label">{s.label}</span>
      </div>
      <p className="scenario-card-desc">{s.desc}</p>
      <button
        className={`scenario-fire-btn scenario-fire-${state}`}
        onClick={fire}
        disabled={state === 'firing'}
      >
        {state === 'idle' && 'Fire →'}
        {state === 'firing' && 'Firing...'}
        {state === 'done' && '✓ Triggered'}
        {state === 'error' && '✗ Failed'}
      </button>
    </div>
  )
}

export default function AdminPage() {
  const [items, setItems] = useState([])
  const [users, setUsers] = useState([])
  const [tab, setTab] = useState('inventory')

  function load() {
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
    fetch(`${API}/users`).then(r => r.json()).then(setUsers).catch(() => {})
  }

  useEffect(() => { load() }, [])

  return (
    <div className="admin-page">
      <div className="admin-header">
        <div className="admin-brand">
          <span>🛍</span>
          <span className="admin-brand-name">TechNest</span>
          <span className="admin-badge">Admin</span>
        </div>
        <p className="admin-subtitle">SentinelAI demo control panel</p>
      </div>

      <div className="admin-tabs">
        {['inventory', 'users', 'scenarios'].map(t => (
          <button
            key={t}
            className={`admin-tab${tab === t ? ' admin-tab-active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === 'inventory' && (
        <Section title="Inventory">
          <InventoryTable items={items} onRefresh={load} />
        </Section>
      )}

      {tab === 'users' && (
        <Section title="Users & Credits">
          <UsersTable users={users} onRefresh={load} />
        </Section>
      )}

      {tab === 'scenarios' && (
        <Section title="Error Scenarios">
          <p className="admin-scenarios-hint">Each button fires a scenario at the target app. Watch the SentinelAI dashboard at <strong>localhost:5173</strong> for the incident feed.</p>
          <div className="scenarios-grid">
            {SCENARIOS.map(s => <ScenarioCard key={s.id} s={s} />)}
          </div>
        </Section>
      )}
    </div>
  )
}
