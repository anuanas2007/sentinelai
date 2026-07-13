import { useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, fmt } from '../utils'

export default function CartPage() {
  const { cart, items, changeQty, removeFromCart } = useApp()
  const navigate = useNavigate()
  const cartItems = Object.entries(cart)
    .map(([name, qty]) => ({ ...items.find(i => i.name === name), qty }))
    .filter(i => i.name)
  const subtotal = cartItems.reduce((s, i) => s + i.price * i.qty, 0)

  if (cartItems.length === 0) {
    return (
      <div className="cart-page">
        <h2 className="page-title">Your Cart</h2>
        <div className="cart-empty">
          <div className="cart-empty-icon">🛒</div>
          <p>Your cart is empty.</p>
          <button className="btn-primary" onClick={() => navigate('/shop')}>Continue Shopping</button>
        </div>
      </div>
    )
  }

  return (
    <div className="cart-page">
      <h2 className="page-title">Your Cart</h2>
      <div className="cart-layout">
        <div className="cart-items">
          {cartItems.map(item => (
            <div key={item.name} className="cart-row">
              <div className="cart-row-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</div>
              <div className="cart-row-info">
                <div className="cart-row-name">{item.display_name}</div>
                <div className="cart-row-price">{fmt(item.price)} each</div>
              </div>
              <div className="qty-control">
                <button onClick={() => changeQty(item.name, item.qty - 1)}>−</button>
                <span>{item.qty}</span>
                <button onClick={() => changeQty(item.name, item.qty + 1)}>+</button>
              </div>
              <div className="cart-row-subtotal">{fmt(item.price * item.qty)}</div>
              <button className="cart-row-remove" onClick={() => removeFromCart(item.name)}>✕</button>
            </div>
          ))}
        </div>

        <div className="cart-summary-box">
          <h3 className="summary-title">Order Summary</h3>
          <div className="summary-rows">
            {cartItems.map(i => (
              <div key={i.name} className="summary-row">
                <span>{i.display_name} × {i.qty}</span>
                <span>{fmt(i.price * i.qty)}</span>
              </div>
            ))}
          </div>
          <div className="summary-divider" />
          <div className="summary-total">
            <span>Total</span>
            <span>{fmt(subtotal)}</span>
          </div>
          <button className="btn-primary btn-full" onClick={() => navigate('/checkout')}>
            Proceed to Checkout →
          </button>
          <button className="btn-ghost btn-full" onClick={() => navigate('/shop')}>
            Continue Shopping
          </button>
        </div>
      </div>
    </div>
  )
}
