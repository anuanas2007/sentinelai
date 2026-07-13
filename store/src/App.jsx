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

const ITEM_CATEGORY = {
  headphones: 'Audio',
  keyboard: 'Peripherals',
  usb_hub: 'Accessories',
  webcam: 'Video',
  mouse_pad: 'Peripherals',
  desk_lamp: 'Lighting',
  ssd: 'Storage',
}

function fmt(n) {
  return `$${Number(n).toFixed(2)}`
}

function stockLabel(stock) {
  if (stock === 0) return { text: 'Out of stock', cls: 'stock-out' }
  if (stock <= 3) return { text: `Only ${stock} left`, cls: 'stock-low' }
  return { text: 'In stock', cls: 'stock-ok' }
}

function Avatar({ name, size = 36 }) {
  const initials = name.split(' ').map(w => w[0]).join('').slice(0, 2)
  const hue = name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360
  return (
    <div className="avatar" style={{ width: size, height: size, background: `hsl(${hue},60%,35%)`, fontSize: size * 0.38 }}>
      {initials}
    </div>
  )
}

// ── Login page ────────────────────────────────────────────────────────────────

function LoginPage({ users, onLogin }) {
  if (!users.length) {
    return (
      <div className="login-page">
        <div className="login-brand">
          <span className="login-logo">🛍</span>
          <h1 className="login-title">TechNest</h1>
        </div>
        <p className="login-sub">Loading...</p>
      </div>
    )
  }

  return (
    <div className="login-page">
      <div className="login-brand">
        <span className="login-logo">🛍</span>
        <h1 className="login-title">TechNest</h1>
        <p className="login-tagline">Premium tech, delivered fast.</p>
      </div>
      <div className="login-card">
        <p className="login-prompt">Choose your account to continue</p>
        <div className="user-cards">
          {users.map(u => (
            <button key={u.id} className="user-card" onClick={() => onLogin(u)}>
              <Avatar name={u.name} size={44} />
              <div className="user-card-info">
                <div className="user-card-name">{u.name}</div>
                <div className="user-card-email">{u.email}</div>
              </div>
              <div className="user-card-credits">
                <div className="credits-val">{fmt(u.balance)}</div>
                <div className="credits-lbl">credits</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

function Header({ user, cartCount, onCartClick, onOrdersClick, onLogoClick }) {
  return (
    <header className="header">
      <button className="header-brand" onClick={onLogoClick}>
        <span className="brand-logo">🛍</span>
        <span className="brand-name">TechNest</span>
      </button>

      <nav className="header-nav">
        <button className="nav-link" onClick={onLogoClick}>Shop</button>
        <button className="nav-link" onClick={onOrdersClick}>Orders</button>
      </nav>

      <div className="header-right">
        <div className="header-user">
          <Avatar name={user.name} size={30} />
          <div className="header-user-info">
            <span className="header-user-name">{user.name.split(' ')[0]}</span>
            <span className="header-credits">{fmt(user.balance)} credits</span>
          </div>
        </div>
        <button className="cart-icon-btn" onClick={onCartClick}>
          <span className="cart-icon-emoji">🛒</span>
          {cartCount > 0 && <span className="cart-count">{cartCount}</span>}
        </button>
      </div>
    </header>
  )
}

// ── Shop page ─────────────────────────────────────────────────────────────────

function ShopPage({ items, onAdd, cartItems }) {
  const [filter, setFilter] = useState('All')
  const categories = ['All', ...new Set(Object.values(ITEM_CATEGORY))]
  const filtered = filter === 'All' ? items : items.filter(i => ITEM_CATEGORY[i.name] === filter)

  return (
    <div className="shop-page">
      <div className="shop-hero">
        <h2 className="shop-hero-title">Tech Essentials</h2>
        <p className="shop-hero-sub">Quality peripherals, accessories, and gear</p>
      </div>

      <div className="shop-filters">
        {categories.map(c => (
          <button
            key={c}
            className={`filter-btn${filter === c ? ' filter-active' : ''}`}
            onClick={() => setFilter(c)}
          >
            {c}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <p className="shop-empty">No products found.</p>
      ) : (
        <div className="product-grid">
          {filtered.map(item => {
            const stock = stockLabel(item.stock)
            const inCart = cartItems[item.name] ?? 0
            return (
              <div key={item.name} className={`product-card${item.stock === 0 ? ' product-out' : ''}`}>
                <div className="product-category">{ITEM_CATEGORY[item.name]}</div>
                <div className="product-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</div>
                <div className="product-name">{item.display_name}</div>
                <div className="product-price">{fmt(item.price)}</div>
                <div className={`stock-pill ${stock.cls}`}>{stock.text}</div>
                {inCart > 0 && (
                  <div className="in-cart-badge">{inCart} in cart</div>
                )}
                <button
                  className="add-btn"
                  disabled={item.stock === 0}
                  onClick={() => onAdd(item)}
                >
                  {item.stock === 0 ? 'Unavailable' : '+ Add to Cart'}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Cart page ─────────────────────────────────────────────────────────────────

function CartPage({ cart, items, onQty, onRemove, onCheckout, onContinue }) {
  const cartItems = Object.entries(cart)
    .map(([name, qty]) => ({ ...items.find(i => i.name === name), qty }))
    .filter(Boolean)

  const subtotal = cartItems.reduce((s, i) => s + i.price * i.qty, 0)

  if (cartItems.length === 0) {
    return (
      <div className="cart-page">
        <h2 className="page-title">Your Cart</h2>
        <div className="cart-empty">
          <div className="cart-empty-icon">🛒</div>
          <p>Your cart is empty.</p>
          <button className="btn-primary" onClick={onContinue}>Continue Shopping</button>
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
                <button onClick={() => onQty(item.name, item.qty - 1)}>−</button>
                <span>{item.qty}</span>
                <button onClick={() => onQty(item.name, item.qty + 1)}>+</button>
              </div>
              <div className="cart-row-subtotal">{fmt(item.price * item.qty)}</div>
              <button className="cart-row-remove" onClick={() => onRemove(item.name)}>✕</button>
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
          <button className="btn-primary btn-full" onClick={onCheckout}>
            Proceed to Checkout →
          </button>
          <button className="btn-ghost btn-full" onClick={onContinue}>
            Continue Shopping
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Checkout page ─────────────────────────────────────────────────────────────

function CheckoutPage({ cart, items, user, onSuccess, onBack }) {
  const cartItems = Object.entries(cart)
    .map(([name, qty]) => ({ ...items.find(i => i.name === name), qty }))
    .filter(Boolean)

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
              body: JSON.stringify({
                user_id: user.id,
                item: item.name,
                quantity: 1,
                payment_method: payMethod,
              }),
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
      onSuccess({ items: cartItems, total: subtotal, payMethod })
    } else {
      setErrors(errs)
    }
  }

  return (
    <div className="checkout-page">
      <button className="back-link" onClick={onBack}>← Back to cart</button>
      <h2 className="page-title">Checkout</h2>

      <div className="checkout-layout">
        <div className="checkout-left">
          {/* Shipping — visual only for demo */}
          <div className="checkout-section">
            <h3 className="section-title">Shipping Address</h3>
            <div className="address-card">
              <div className="address-name">{user.name}</div>
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
                    Available: {fmt(user.balance)}
                    {subtotal > user.balance && <span className="pay-warning"> — insufficient for this order</span>}
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
            {loading ? <span className="loading-dots">Processing<span>...</span></span> : `Place Order · ${fmt(subtotal)}`}
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

// ── Confirmation page ─────────────────────────────────────────────────────────

function ConfirmationPage({ order, onContinue }) {
  const orderId = `TN-${Math.floor(Math.random() * 900000 + 100000)}`
  return (
    <div className="confirmation-page">
      <div className="confirmation-card">
        <div className="confirmation-icon">✓</div>
        <h2 className="confirmation-title">Order Placed!</h2>
        <p className="confirmation-sub">Order #{orderId} is confirmed.</p>
        <div className="confirmation-items">
          {order.items.map(i => (
            <div key={i.name} className="conf-item">
              <span>{ITEM_EMOJI[i.name] ?? '📦'}</span>
              <span>{i.display_name} × {i.qty}</span>
              <span>{fmt(i.price * i.qty)}</span>
            </div>
          ))}
        </div>
        <div className="confirmation-total">
          Total paid: <strong>{fmt(order.total)}</strong> via {order.payMethod === 'card' ? 'Credit Card' : 'Store Credits'}
        </div>
        <button className="btn-primary" onClick={onContinue}>Continue Shopping</button>
      </div>
    </div>
  )
}

// ── Orders history page ───────────────────────────────────────────────────────

function OrdersPage({ user }) {
  const [orders, setOrders] = useState(null)

  useEffect(() => {
    if (!user) return
    setOrders(null)
    fetch(`${API}/users/${user.id}/orders`)
      .then(r => r.json())
      .then(setOrders)
      .catch(() => setOrders([]))
  }, [user?.id])

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

// ── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [users, setUsers] = useState([])
  const [items, setItems] = useState([])
  const [activeUser, setActiveUser] = useState(null)
  const [cart, setCart] = useState({})
  const [page, setPage] = useState('login') // login | shop | cart | checkout | confirmation | orders
  const [lastOrder, setLastOrder] = useState(null)

  useEffect(() => {
    fetch(`${API}/users`).then(r => r.json()).then(setUsers).catch(() => {})
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
  }, [])

  const refreshData = useCallback(() => {
    fetch(`${API}/users`).then(r => r.json()).then(data => {
      setUsers(data)
      if (activeUser) setActiveUser(u => data.find(d => d.id === u?.id) ?? u)
    }).catch(() => {})
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
  }, [activeUser])

  function login(user) {
    setActiveUser(user)
    setPage('shop')
  }

  function addToCart(item) {
    setCart(c => ({ ...c, [item.name]: (c[item.name] ?? 0) + 1 }))
  }

  function changeQty(name, qty) {
    if (qty <= 0) setCart(c => { const n = { ...c }; delete n[name]; return n })
    else setCart(c => ({ ...c, [name]: qty }))
  }

  function removeFromCart(name) {
    setCart(c => { const n = { ...c }; delete n[name]; return n })
  }

  function handleOrderSuccess(order) {
    setLastOrder(order)
    setCart({})
    refreshData()
    setPage('confirmation')
  }

  const cartCount = Object.values(cart).reduce((s, n) => s + n, 0)

  if (page === 'login') {
    return <LoginPage users={users} onLogin={login} />
  }

  return (
    <div className="app">
      <Header
        user={activeUser}
        cartCount={cartCount}
        onCartClick={() => setPage('cart')}
        onOrdersClick={() => setPage('orders')}
        onLogoClick={() => setPage('shop')}
      />
      <main className="main-content">
        {page === 'shop' && (
          <ShopPage items={items} onAdd={addToCart} cartItems={cart} />
        )}
        {page === 'cart' && (
          <CartPage
            cart={cart}
            items={items}
            onQty={changeQty}
            onRemove={removeFromCart}
            onCheckout={() => setPage('checkout')}
            onContinue={() => setPage('shop')}
          />
        )}
        {page === 'checkout' && (
          <CheckoutPage
            cart={cart}
            items={items}
            user={activeUser}
            onSuccess={handleOrderSuccess}
            onBack={() => setPage('cart')}
          />
        )}
        {page === 'confirmation' && lastOrder && (
          <ConfirmationPage
            order={lastOrder}
            onContinue={() => { setPage('shop') }}
          />
        )}
        {page === 'orders' && (
          <OrdersPage user={activeUser} />
        )}
      </main>
    </div>
  )
}
