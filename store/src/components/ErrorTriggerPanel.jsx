import { useState } from 'react'
import { API, AGENT_API } from '../utils'

const SCENARIOS = [
  {
    id: 'negative_balance',
    name: 'Negative Balance Race',
    desc: '30 concurrent orders for Alice — balance goes negative',
    ai: true,
    run: async () => {
      // Reset Alice's balance first so the race is reproducible
      await fetch(`${API}/admin/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, amount: 100 }),
      })
      await fetch(`${API}/admin/set_stock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: 'headphones', stock: 50 }),
      })
      await Promise.all(Array.from({ length: 30 }, () =>
        fetch(`${API}/orders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: 1, item: 'headphones', quantity: 1, payment_method: 'credits' }),
        }).catch(() => {})
      ))
    },
  },
  {
    id: 'stock_race',
    name: 'Stock Race',
    desc: 'Reset keyboard to 3 stock, 50 concurrent orders — stock goes negative',
    ai: true,
    run: async () => {
      await fetch(`${API}/admin/set_stock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: 'keyboard', stock: 3 }),
      })
      await fetch(`${API}/admin/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, amount: 99999 }),
      })
      await Promise.all(Array.from({ length: 50 }, () =>
        fetch(`${API}/orders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: 1, item: 'keyboard', quantity: 1, payment_method: 'credits' }),
        }).catch(() => {})
      ))
    },
  },
  {
    id: 'analytics_crash',
    name: 'Analytics Crash',
    desc: 'Division by zero in /analytics — 30% rate × 10 calls',
    ai: true,
    run: async () => {
      await Promise.all(Array.from({ length: 10 }, () =>
        fetch(`${API}/analytics`).catch(() => {})
      ))
    },
  },
  {
    id: 'external_timeout',
    name: 'External Timeout',
    desc: '5s delay endpoint with 3s timeout — guaranteed failure × 5 calls',
    ai: true,
    run: async () => {
      await Promise.all(Array.from({ length: 5 }, () =>
        fetch(`${API}/external`).catch(() => {})
      ))
    },
  },
  {
    id: 'payment_cascade',
    name: 'Payment Cascade',
    desc: 'Slow payment service under concurrent load → pool exhaustion',
    ai: true,
    run: async () => {
      // Bob has $0, webcam has 0 stock — alternating failures cascade
      await fetch(`${API}/admin/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 2, amount: 0 }),
      })
      await fetch(`${API}/admin/set_stock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: 'webcam', stock: 0 }),
      })
      const bobOrders = Array.from({ length: 8 }, () =>
        fetch(`${API}/orders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: 2, item: 'headphones', quantity: 1, payment_method: 'credits' }),
        }).catch(() => {})
      )
      const noStockOrders = Array.from({ length: 8 }, () =>
        fetch(`${API}/orders`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: 1, item: 'webcam', quantity: 1, payment_method: 'credits' }),
        }).catch(() => {})
      )
      await Promise.all([...bobOrders, ...noStockOrders])
    },
  },
  {
    id: 'insufficient_balance',
    name: 'Insufficient Balance',
    desc: 'Bob tries to buy headphones with $0 credits — immediate, no AI',
    ai: false,
    run: async () => {
      await fetch(`${API}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 2, item: 'headphones', quantity: 1, payment_method: 'credits' }),
      }).catch(() => {})
    },
  },
  {
    id: 'out_of_stock',
    name: 'Out of Stock',
    desc: 'Order a sold-out item — immediate rejection, no AI',
    ai: false,
    run: async () => {
      await fetch(`${API}/admin/set_stock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: 'webcam', stock: 0 }),
      })
      await fetch(`${API}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, item: 'webcam', quantity: 1, payment_method: 'credits' }),
      }).catch(() => {})
    },
  },
]

export default function ErrorTriggerPanel() {
  const [open, setOpen] = useState(false)
  const [running, setRunning] = useState(null)
  const [toast, setToast] = useState(null)

  async function fire(scenario) {
    if (running) return
    setRunning(scenario.id)
    setToast(null)
    try {
      await scenario.run()
      setToast({ type: 'success', msg: `Fired: ${scenario.name}. Watch SentinelAI →` })
    } catch {
      setToast({ type: 'error', msg: 'Failed to trigger. Is the app running?' })
    } finally {
      setRunning(null)
      setTimeout(() => setToast(null), 5000)
    }
  }

  const aiScenarios    = SCENARIOS.filter(s => s.ai)
  const noAiScenarios  = SCENARIOS.filter(s => !s.ai)

  return (
    <div className="trigger-panel-wrapper">
      {open && (
        <div className="trigger-panel">
          <div className="trigger-panel-header">
            <div>
              <div className="trigger-panel-title">
                <span className="trigger-panel-title-dot" />
                Error Scenarios
              </div>
              <div className="trigger-panel-sub">Triggers for SentinelAI demo</div>
            </div>
            <button className="trigger-panel-close" onClick={() => setOpen(false)}>✕</button>
          </div>

          {toast && (
            <div className={`trigger-toast trigger-toast-${toast.type}`}>
              {toast.type === 'success' ? '✓' : '✗'} {toast.msg}
            </div>
          )}

          <div className="trigger-list">
            <div className="trigger-section-label">AI Investigation</div>
            {aiScenarios.map(s => (
              <div className="trigger-row" key={s.id}>
                <div className="trigger-row-info">
                  <div className="trigger-row-name">{s.name}</div>
                  <div className="trigger-row-desc">{s.desc}</div>
                </div>
                <span className="trigger-ai-badge trigger-ai-yes">AI</span>
                <button
                  className="trigger-btn"
                  disabled={!!running}
                  onClick={() => fire(s)}
                  title="Trigger this scenario"
                >
                  {running === s.id ? '…' : '▶'}
                </button>
              </div>
            ))}

            <div className="trigger-section-label" style={{ marginTop: 8 }}>No AI — Routing contrast</div>
            {noAiScenarios.map(s => (
              <div className="trigger-row" key={s.id}>
                <div className="trigger-row-info">
                  <div className="trigger-row-name">{s.name}</div>
                  <div className="trigger-row-desc">{s.desc}</div>
                </div>
                <span className="trigger-ai-badge trigger-ai-no">No AI</span>
                <button
                  className={`trigger-btn trigger-btn-noai`}
                  disabled={!!running}
                  onClick={() => fire(s)}
                  title="Trigger this scenario"
                >
                  {running === s.id ? '…' : '▶'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <button className="trigger-tab" onClick={() => setOpen(o => !o)}>
        <span>⚡</span>
        <span>Scenarios</span>
      </button>
    </div>
  )
}
