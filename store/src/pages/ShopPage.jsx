import { useState } from 'react'
import { useApp } from '../context/AppContext'
import { ITEM_EMOJI, ITEM_CATEGORY, fmt, stockLabel } from '../utils'

export default function ShopPage() {
  const { items, cart, addToCart } = useApp()
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
                  {inCart > 0 && <div className="in-cart-badge">{inCart} in cart</div>}
                  <button
                    className="add-btn"
                    disabled={item.stock === 0}
                    onClick={() => addToCart(item)}
                  >
                    {item.stock === 0 ? 'Unavailable' : '+ Add to Cart'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
