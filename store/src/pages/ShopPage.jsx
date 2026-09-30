import { useState } from 'react'
import { useApp } from '../context/AppContext'
import { ITEM_IMAGE, ITEM_CATEGORY, fmt, stockLabel } from '../utils'

export default function ShopPage() {
  const { items, cart, addToCart, changeQty, loadError } = useApp()
  const [filter, setFilter] = useState('All')

  if (loadError) {
    return (
      <div style={{ padding: '60px 0', textAlign: 'center' }}>
        <p style={{ color: 'var(--red)', fontWeight: 600, fontSize: 16 }}>
          We're having trouble loading products right now.
        </p>
        <p style={{ marginTop: 8, color: 'var(--text-secondary)', fontSize: 14 }}>
          Please try refreshing the page. If the problem persists, the store may be temporarily unavailable.
        </p>
      </div>
    )
  }

  const categories = ['All', ...new Set(Object.values(ITEM_CATEGORY))]
  const filtered = filter === 'All' ? items : items.filter(i => ITEM_CATEGORY[i.name] === filter)

  return (
    <div className="shop-page">
      <div className="shop-banner">
        <div className="shop-banner-text">
          <div className="shop-banner-tag">New arrivals</div>
          <div className="shop-banner-title">Tech Essentials</div>
          <div className="shop-banner-sub">Premium peripherals, accessories, and gear</div>
        </div>
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
        <p className="shop-empty">No products in this category.</p>
      ) : (
        <div className="product-grid">
          {filtered.map(item => {
            const stock = stockLabel(item.stock)
            const inCart = cart[item.name] ?? 0
            return (
              <div key={item.name} className={`product-card${item.stock === 0 ? ' product-out' : ''}`}>
                <img
                  className="product-img"
                  src={ITEM_IMAGE[item.name]}
                  alt={item.display_name}
                  loading="lazy"
                  onError={e => { e.target.style.background = '#f1f5f9' }}
                />
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
                      {item.stock === 0 ? 'Unavailable' : 'Add to Cart'}
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
