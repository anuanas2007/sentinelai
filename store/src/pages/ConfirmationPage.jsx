import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, fmt } from '../utils'

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
        <h2 className="confirmation-title">Order Placed!</h2>
        <p className="confirmation-sub">Order #{orderId} is confirmed.</p>
        <div className="confirmation-items">
          {lastOrder.items.map(i => (
            <div key={i.name} className="conf-item">
              <span>{ITEM_EMOJI[i.name] ?? '📦'}</span>
              <span>{i.display_name} × {i.qty}</span>
              <span>{fmt(i.price * i.qty)}</span>
            </div>
          ))}
        </div>
        <div className="confirmation-total">
          Total paid: <strong>{fmt(lastOrder.total)}</strong> via {lastOrder.payMethod === 'card' ? 'Credit Card' : 'Store Credits'}
        </div>
        <button className="btn-primary" onClick={() => navigate('/shop')}>Continue Shopping</button>
      </div>
    </div>
  )
}
