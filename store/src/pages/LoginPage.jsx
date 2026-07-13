import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import Avatar from '../components/Avatar'
import { fmt } from '../utils'

export default function LoginPage() {
  const { users, activeUser, setActiveUser } = useApp()
  const navigate = useNavigate()

  useEffect(() => {
    if (activeUser) navigate('/shop', { replace: true })
  }, [activeUser])

  function login(user) {
    setActiveUser(user)
    navigate('/shop')
  }

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
            <button key={u.id} className="user-card" onClick={() => login(u)}>
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
