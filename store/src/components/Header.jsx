import { useNavigate, useLocation } from 'react-router-dom'
import Avatar from './Avatar'
import { useApp } from '../context/AppContext'
import { fmt } from '../utils'

export default function Header() {
  const { activeUser, cartCount } = useApp()
  const navigate = useNavigate()
  const { pathname } = useLocation()

  return (
    <header className="header">
      <button className="header-brand" onClick={() => navigate('/shop')}>
        <span className="brand-wordmark">TechNest</span>
        <span className="brand-dot" />
      </button>

      <nav className="header-nav">
        <button
          className={`nav-link${pathname === '/shop' ? ' active' : ''}`}
          onClick={() => navigate('/shop')}
        >
          Shop
        </button>
        <button
          className={`nav-link${pathname === '/orders' ? ' active' : ''}`}
          onClick={() => navigate('/orders')}
        >
          Orders
        </button>
      </nav>

      <div className="header-right">
        <div className="header-user">
          <Avatar name={activeUser.name} size={32} />
          <div className="header-user-info">
            <span className="header-user-name">{activeUser.name.split(' ')[0]}</span>
            <span className="header-credits">{fmt(activeUser.balance)} credits</span>
          </div>
        </div>

        <button className="cart-btn" onClick={() => navigate('/cart')}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/>
            <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>
          </svg>
          Cart
          {cartCount > 0 && <span className="cart-badge">{cartCount}</span>}
        </button>
      </div>
    </header>
  )
}
