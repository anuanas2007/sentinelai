import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { ITEM_IMAGE, fmt } from '../utils'

export default function ConfirmationPage() {
  const { lastOrder } = useApp()
  const navigate = useNavigate()
  const orderId = useRef(`TN-${Math.floor(Math.random() * 900000 + 100000)}`).current

  useEffect(() => {
    if (!lastOrder) navigate('/shop', { replace: true })
  }, [])

  if (!lastOrder) return null

  return (
    <div className="confirmation-page">
      <div className="confirmation-card">
        <div className="confirmation-icon">✓</div>
        <h2 className="confirmation-title">Order Confirmed!</h2>
        <p className="confirmation-sub">Order #{orderId} has been placed successfully.</p>

        <div className="confirmation-items">
          {lastOrder.items.map(i => (
            <div key={i.name} className="conf-item">
              <img className="conf-item-img" src={ITEM_IMAGE[i.name]} alt={i.display_name} />
              <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{i.display_name} × {i.qty}</span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{fmt(i.price * i.qty)}</span>
            </div>
          ))}
        </div>

        <div className="confirmation-total">
          Total paid: <strong>{fmt(lastOrder.total)}</strong> via {lastOrder.payMethod === 'card' ? 'Credit Card' : 'Store Credits'}
        </div>

        <div className="email-status">
          <span className="email-status-dot" />
          Confirmation email sent
        </div>

        <button className="btn-primary btn-full" onClick={() => navigate('/shop')}>
          Continue Shopping
        </button>
      </div>
    </div>
  )
}
