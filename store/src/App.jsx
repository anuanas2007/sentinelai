import { useEffect, useState, useCallback } from 'react'
import './App.css'

const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

const ITEM_EMOJI = {
  headphones: '🎧',
  keyboard: '⌨️',
  usb_hub: '🔌',
  webcam: '📷',
  mouse_pad: '🖱️',
  desk_lamp: '💡',
  ssd: '💾',
}

function stockLabel(stock) {
  if (stock === 0) return { text: 'Out of stock', cls: 'stock-out' }
  if (stock <= 3) return { text: `Only ${stock} left`, cls: 'stock-low' }
  return { text: 'In stock', cls: 'stock-ok' }
}

function fmt(n) {
  return `$${Number(n).toFixed(2)}`
}

// ── Header ──────────────────────────────────────────────────────────────────

function Header({ users, activeUser, onUserChange, cartCount, onCartOpen, page, onPage }) {
  return (
    <header className="header">
      <div className="header-brand" onClick={() => onPage('shop')} style={{ cursor: 'pointer' }}>
        <span className="brand-logo">🛍</span>
        <span className="brand-name">TechNest</span>
      </div>
      <nav className="header-nav">
        <button className={`nav-btn${page === 'shop' ? ' nav-active' : ''}`} onClick={() => onPage('shop')}>Shop</button>
        <button className={`nav-btn${page === 'orders' ? ' nav-active' : ''}`} onClick={() => onPage('orders')}>My Orders</button>
      </nav>
      <div className="header-right">
        {activeUser && (
          <div className="credits-badge">
            <span className="credits-icon">💳</span>
            <span className="credits-amount">{fmt(activeUser.balance)}</span>
            <span className="credits-label">credits</span>
          </div>
        )}
        <div className="user-select-wrap">
          <span className="user-select-label">Signed in as</span>
          <select
            className="user-select"
            value={activeUser?.id ?? ''}
            onChange={e => onUserChange(Number(e.target.value))}
          >
            {users.map(u => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
        </div>
        <button className="cart-btn" onClick={onCartOpen}>
          🛒
          {cartCount > 0 && <span className="cart-badge">{cartCount}</span>}
        </button>
      </div>
    </header>
  )
}

// ── Product card ─────────────────────────────────────────────────────────────

function ProductCard({ item, onAdd }) {
  const stock = stockLabel(item.stock)
  const disabled = item.stock === 0
  return (
    <div className={`product-card${disabled ? ' product-disabled' : ''}`}>
      <div className="product-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</div>
      <div className="product-info">
        <div className="product-name">{item.display_name}</div>
        <div className="product-price">{fmt(item.price)}</div>
        <span className={`stock-badge ${stock.cls}`}>{stock.text}</span>
      </div>
      <button
        className="add-btn"
        disabled={disabled}
        onClick={() => onAdd(item)}
      >
        {disabled ? 'Unavailable' : 'Add to cart'}
      </button>
    </div>
  )
}

// ── Cart drawer ───────────────────────────────────────────────────────────────

function CartDrawer({ cart, items, onClose, onQtyChange, onRemove, activeUser, onCheckout }) {
  const cartItems = Object.entries(cart)
    .map(([name, qty]) => ({ ...items.find(i => i.name === name), qty }))
    .filter(Boolean)

  const total = cartItems.reduce((s, i) => s + i.price * i.qty, 0)
  const [payMethod, setPayMethod] = useState('credits')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null) // { success: [...], errors: [...] }

  async function checkout() {
    setLoading(true)
    setResult(null)
    const successes = [], errors = []
    await Promise.all(
      cartItems.map(async item => {
        for (let i = 0; i < item.qty; i++) {
          try {
            const res = await fetch(`${API}/orders`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                user_id: activeUser.id,
                item: item.name,
                quantity: 1,
                payment_method: payMethod,
              }),
            })
            if (res.ok) successes.push(item.display_name)
            else {
              const d = await res.json()
              errors.push(`${item.display_name}: ${d.detail || res.statusText}`)
            }
          } catch (e) {
            errors.push(`${item.display_name}: network error`)
          }
        }
      })
    )
    setLoading(false)
    setResult({ successes, errors })
    if (successes.length > 0) onCheckout()
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer" onClick={e => e.stopPropagation()}>
        <div className="drawer-header">
          <h2>Your Cart</h2>
          <button className="drawer-close" onClick={onClose}>✕</button>
        </div>

        {cartItems.length === 0 ? (
          <p className="drawer-empty">Your cart is empty.</p>
        ) : (
          <>
            <div className="drawer-items">
              {cartItems.map(item => (
                <div key={item.name} className="cart-item">
                  <span className="cart-item-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</span>
                  <div className="cart-item-info">
                    <div className="cart-item-name">{item.display_name}</div>
                    <div className="cart-item-price">{fmt(item.price)} each</div>
                  </div>
                  <div className="cart-item-qty">
                    <button onClick={() => onQtyChange(item.name, item.qty - 1)}>−</button>
                    <span>{item.qty}</span>
                    <button onClick={() => onQtyChange(item.name, item.qty + 1)}>+</button>
                  </div>
                  <div className="cart-item-subtotal">{fmt(item.price * item.qty)}</div>
                  <button className="cart-item-remove" onClick={() => onRemove(item.name)}>✕</button>
                </div>
              ))}
            </div>

            <div className="drawer-footer">
              <div className="cart-total">
                <span>Total</span>
                <span>{fmt(total)}</span>
              </div>

              <div className="pay-method">
                <span className="pay-method-label">Pay with</span>
                <div className="pay-method-options">
                  <label className={`pay-option${payMethod === 'credits' ? ' pay-selected' : ''}`}>
                    <input type="radio" value="credits" checked={payMethod === 'credits'} onChange={() => setPayMethod('credits')} />
                    <span>💳 Store Credits</span>
                    {activeUser && <span className="pay-balance">({fmt(activeUser.balance)} available)</span>}
                  </label>
                  <label className={`pay-option${payMethod === 'card' ? ' pay-selected' : ''}`}>
                    <input type="radio" value="card" checked={payMethod === 'card'} onChange={() => setPayMethod('card')} />
                    <span>💳 Credit Card</span>
                  </label>
                </div>
              </div>

              {result && (
                <div className="checkout-result">
                  {result.successes.length > 0 && (
                    <div className="result-ok">✓ Ordered: {result.successes.join(', ')}</div>
                  )}
                  {result.errors.map((e, i) => (
                    <div key={i} className="result-err">✗ {e}</div>
                  ))}
                </div>
              )}

              <button
                className="checkout-btn"
                onClick={checkout}
                disabled={loading}
              >
                {loading ? 'Processing...' : `Checkout · ${fmt(total)}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Order history ─────────────────────────────────────────────────────────────

function OrdersPage({ activeUser }) {
  const [orders, setOrders] = useState(null)

  useEffect(() => {
    if (!activeUser) return
    setOrders(null)
    fetch(`${API}/users/${activeUser.id}/orders`)
      .then(r => r.json())
      .then(setOrders)
      .catch(() => setOrders([]))
  }, [activeUser?.id])

  if (!activeUser) return null

  return (
    <div className="orders-page">
      <h2 className="orders-title">Order History — {activeUser.name}</h2>
      {orders === null && <p className="orders-loading">Loading...</p>}
      {orders?.length === 0 && <p className="orders-empty">No orders yet.</p>}
      {orders && orders.length > 0 && (
        <div className="orders-list">
          {orders.map(o => (
            <div key={o.id} className="order-row">
              <span className="order-emoji">{ITEM_EMOJI[o.item_name] ?? '📦'}</span>
              <div className="order-info">
                <div className="order-name">{o.display_name}</div>
                <div className="order-meta">
                  qty {o.quantity} · {o.payment_method === 'card' ? 'Credit Card' : 'Store Credits'}
                </div>
              </div>
              <div className="order-right">
                <div className="order-total">{fmt(o.total_charged)}</div>
                <div className="order-date">
                  {new Date(o.created_at).toLocaleString()}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Root app ──────────────────────────────────────────────────────────────────

export default function App() {
  const [items, setItems] = useState([])
  const [users, setUsers] = useState([])
  const [activeUserId, setActiveUserId] = useState(null)
  const [cart, setCart] = useState({}) // { item_name: qty }
  const [cartOpen, setCartOpen] = useState(false)
  const [page, setPage] = useState('shop')

  useEffect(() => {
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
    fetch(`${API}/users`).then(r => r.json()).then(data => {
      setUsers(data)
      if (data.length > 0) setActiveUserId(data[0].id)
    }).catch(() => {})
  }, [])

  // Refresh user credits after checkout
  const refreshUsers = useCallback(() => {
    fetch(`${API}/users`).then(r => r.json()).then(setUsers).catch(() => {})
  }, [])

  const activeUser = users.find(u => u.id === activeUserId) ?? null

  function addToCart(item) {
    setCart(c => ({ ...c, [item.name]: (c[item.name] ?? 0) + 1 }))
    setCartOpen(true)
  }

  function changeQty(name, qty) {
    if (qty <= 0) {
      setCart(c => { const n = { ...c }; delete n[name]; return n })
    } else {
      setCart(c => ({ ...c, [name]: qty }))
    }
  }

  function removeFromCart(name) {
    setCart(c => { const n = { ...c }; delete n[name]; return n })
  }

  function afterCheckout() {
    setCart({})
    refreshUsers()
    // Refresh items too so stock counts update
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
  }

  const cartCount = Object.values(cart).reduce((s, n) => s + n, 0)

  return (
    <div className="app">
      <Header
        users={users}
        activeUser={activeUser}
        onUserChange={setActiveUserId}
        cartCount={cartCount}
        onCartOpen={() => setCartOpen(true)}
        page={page}
        onPage={setPage}
      />

      {page === 'shop' && (
        <main className="shop-main">
          <div className="shop-heading">
            <h1>Tech Essentials</h1>
            <p className="shop-sub">{items.length} products</p>
          </div>
          <div className="product-grid">
            {items.map(item => (
              <ProductCard key={item.name} item={item} onAdd={addToCart} />
            ))}
          </div>
        </main>
      )}

      {page === 'orders' && (
        <main className="shop-main">
          <OrdersPage activeUser={activeUser} />
        </main>
      )}

      {cartOpen && (
        <CartDrawer
          cart={cart}
          items={items}
          onClose={() => setCartOpen(false)}
          onQtyChange={changeQty}
          onRemove={removeFromCart}
          activeUser={activeUser}
          onCheckout={afterCheckout}
        />
      )}
    </div>
  )
}
