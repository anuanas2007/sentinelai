import { useEffect, useState } from 'react'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, fmt, API } from '../utils'

export default function OrdersPage() {
  const { activeUser } = useApp()
  const [orders, setOrders] = useState(null)

  useEffect(() => {
    if (!activeUser) return
    setOrders(null)
    fetch(`${API}/users/${activeUser.id}/orders`)
      .then(r => r.json())
      .then(setOrders)
      .catch(() => setOrders([]))
  }, [activeUser?.id])

  return (
    <div className="orders-page">
      <h2 className="page-title">Order History</h2>
      {orders === null && <p className="page-loading">Loading orders...</p>}
      {orders?.length === 0 && <p className="page-empty">No orders yet. Go shop!</p>}
      {orders && orders.length > 0 && (
        <div className="orders-list">
          {orders.map(o => (
            <div key={o.id} className="order-card">
              <div className="order-card-emoji">{ITEM_EMOJI[o.item_name] ?? '📦'}</div>
              <div className="order-card-info">
                <div className="order-card-name">{o.display_name}</div>
                <div className="order-card-meta">Qty {o.quantity} · {o.payment_method === 'card' ? 'Credit Card' : 'Store Credits'}</div>
              </div>
              <div className="order-card-right">
                <div className="order-card-total">{fmt(o.total_charged)}</div>
                <div className="order-card-date">{new Date(o.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
