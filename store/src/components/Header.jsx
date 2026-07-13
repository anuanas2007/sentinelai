import { useNavigate } from 'react-router-dom'
import Avatar from './Avatar'
import { useApp } from '../context/AppContext'
import { fmt } from '../utils'

export default function Header() {
  const { activeUser, cartCount } = useApp()
  const navigate = useNavigate()

  return (
    <header className="header">
      <button className="header-brand" onClick={() => navigate('/shop')}>
        <span className="brand-logo">🛍</span>
        <span className="brand-name">TechNest</span>
      </button>

      <nav className="header-nav">
        <button className="nav-link" onClick={() => navigate('/shop')}>Shop</button>
        <button className="nav-link" onClick={() => navigate('/orders')}>Orders</button>
      </nav>

      <div className="header-right">
        <div className="header-user">
          <Avatar name={activeUser.name} size={30} />
          <div className="header-user-info">
            <span className="header-user-name">{activeUser.name.split(' ')[0]}</span>
            <span className="header-credits">{fmt(activeUser.balance)} credits</span>
          </div>
        </div>
        <button className="cart-icon-btn" onClick={() => navigate('/cart')}>
          🛒
          {cartCount > 0 && <span className="cart-count">{cartCount}</span>}
        </button>
      </div>
    </header>
  )
}
