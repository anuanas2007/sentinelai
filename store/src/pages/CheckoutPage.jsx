import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, fmt, API } from '../utils'

export default function CheckoutPage() {
  const { cart, items, activeUser, clearCart, setLastOrder, refreshData } = useApp()
  const navigate = useNavigate()
  const cartItems = Object.entries(cart)
    .map(([name, qty]) => ({ ...items.find(i => i.name === name), qty }))
    .filter(i => i.name)
  const subtotal = cartItems.reduce((s, i) => s + i.price * i.qty, 0)
  const [payMethod, setPayMethod] = useState('credits')
  const [loading, setLoading] = useState(false)
  const [errors, setErrors] = useState([])

  async function placeOrder() {
    setLoading(true)
    setErrors([])
    const errs = []
    await Promise.all(
      cartItems.map(async item => {
        for (let i = 0; i < item.qty; i++) {
          try {
            const res = await fetch(`${API}/orders`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ user_id: activeUser.id, item: item.name, quantity: 1, payment_method: payMethod }),
            })
            if (!res.ok) {
              const d = await res.json()
              errs.push(`${item.display_name}: ${d.detail || res.statusText}`)
            }
          } catch {
            errs.push(`${item.display_name}: network error`)
          }
        }
      })
    )
    setLoading(false)
    if (errs.length === 0) {
      setLastOrder({ items: cartItems, total: subtotal, payMethod })
      clearCart()
      refreshData()
      navigate('/confirmation')
    } else {
      setErrors(errs)
    }
  }

  return (
    <div className="checkout-page">
      <button className="back-link" onClick={() => navigate('/cart')}>← Back to cart</button>
      <h2 className="page-title">Checkout</h2>

      <div className="checkout-layout">
        <div className="checkout-left">
          <div className="checkout-section">
            <h3 className="section-title">Shipping Address</h3>
            <div className="address-card">
              <div className="address-name">{activeUser.name}</div>
              <div className="address-line">123 Demo Street</div>
              <div className="address-line">San Francisco, CA 94107</div>
              <div className="address-tag">Default address</div>
            </div>
          </div>

          <div className="checkout-section">
            <h3 className="section-title">Payment Method</h3>
            <div className="pay-options">
              <label className={`pay-option${payMethod === 'credits' ? ' pay-selected' : ''}`}>
                <input type="radio" value="credits" checked={payMethod === 'credits'} onChange={() => setPayMethod('credits')} />
                <div className="pay-option-body">
                  <div className="pay-option-name">💳 Store Credits</div>
                  <div className="pay-option-detail">
                    Available: {fmt(activeUser.balance)}
                    {subtotal > activeUser.balance && <span className="pay-warning"> — insufficient for this order</span>}
                  </div>
                </div>
              </label>
              <label className={`pay-option${payMethod === 'card' ? ' pay-selected' : ''}`}>
                <input type="radio" value="card" checked={payMethod === 'card'} onChange={() => setPayMethod('card')} />
                <div className="pay-option-body">
                  <div className="pay-option-name">💳 Credit Card</div>
                  <div className="pay-option-detail">Visa ending in 4242</div>
                </div>
              </label>
            </div>
          </div>

          {errors.length > 0 && (
            <div className="checkout-errors">
              {errors.map((e, i) => <div key={i} className="error-line">✗ {e}</div>)}
            </div>
          )}

          <button className="btn-primary btn-large btn-full" onClick={placeOrder} disabled={loading}>
            {loading ? 'Processing...' : `Place Order · ${fmt(subtotal)}`}
          </button>
        </div>

        <div className="checkout-right">
          <div className="order-summary-box">
            <h3 className="section-title">Order Summary</h3>
            {cartItems.map(i => (
              <div key={i.name} className="summary-item">
                <span className="summary-item-emoji">{ITEM_EMOJI[i.name] ?? '📦'}</span>
                <span className="summary-item-name">{i.display_name} × {i.qty}</span>
                <span className="summary-item-price">{fmt(i.price * i.qty)}</span>
              </div>
            ))}
            <div className="summary-divider" />
            <div className="summary-line">
              <span>Subtotal</span><span>{fmt(subtotal)}</span>
            </div>
            <div className="summary-line">
              <span>Shipping</span><span className="free-badge">Free</span>
            </div>
            <div className="summary-divider" />
            <div className="summary-total-line">
              <span>Total</span><span>{fmt(subtotal)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
