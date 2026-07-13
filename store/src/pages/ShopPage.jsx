import { useState } from 'react'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, ITEM_CATEGORY, fmt, stockLabel } from '../utils'

export default function ShopPage() {
  const { items, cart, addToCart, changeQty, loadError } = useApp()

  if (loadError) {
    return (
      <div style={{ padding: '40px 0', color: 'var(--red)' }}>
        <strong>Could not load products:</strong> {loadError}
        <p style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 13 }}>
          Run <code style={{ background: 'var(--surface-raised)', padding: '2px 6px', borderRadius: 4 }}>docker compose down -v &amp;&amp; docker compose up --build</code> to reset the database schema.
        </p>
      </div>
    )
  }
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
            const inCart = cart[item.name] ?? 0
            const catClass = `img-${(ITEM_CATEGORY[item.name] || 'accessories').toLowerCase()}`
            return (
              <div key={item.name} className={`product-card${item.stock === 0 ? ' product-out' : ''}`}>
                <div className={`product-img ${catClass}`}>
                  <span className="product-emoji">{ITEM_EMOJI[item.name] ?? '📦'}</span>
                </div>
                <div className="product-card-body">
                  <div className="product-category">{ITEM_CATEGORY[item.name]}</div>
                  <div className="product-name">{item.display_name}</div>
                  <div className="product-price">{fmt(item.price)}</div>
                  <div className={`stock-pill ${stock.cls}`}>{stock.text}</div>
                  {inCart > 0 ? (
                    <div className="card-qty-control">
                      <button onClick={() => changeQty(item.name, inCart - 1)}>−</button>
                      <span>{inCart} in cart</span>
                      <button onClick={() => changeQty(item.name, inCart + 1)}>+</button>
                    </div>
                  ) : (
                    <button
                      className="add-btn"
                      disabled={item.stock === 0}
                      onClick={() => addToCart(item)}
                    >
                      {item.stock === 0 ? 'Unavailable' : '+ Add to Cart'}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
