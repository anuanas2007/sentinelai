import { useEffect, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { fmt } from '../utils'

const DEMO_PASSWORD = 'password123'

export default function LoginPage() {
  const { users, activeUser, setActiveUser } = useApp()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [showHints, setShowHints] = useState(false)

  useEffect(() => {
    if (activeUser) navigate('/shop', { replace: true })
  }, [activeUser])

  function handleSubmit(e) {
    e.preventDefault()
    setError('')
    const user = users.find(u => u.email.toLowerCase() === email.trim().toLowerCase())
    if (!user) {
      setError('No account found with that email.')
      return
    }
    // Seeded demo accounts use password123; signed-up accounts store their
    // password in sessionStorage (demo-only — no real auth server).
    const stored = sessionStorage.getItem(`pw:${user.id}`)
    const expected = stored ?? DEMO_PASSWORD
    if (password !== expected) {
      setError('Incorrect password.')
      return
    }
    setActiveUser(user)
    navigate('/shop')
  }

  function fillDemo(u) {
    setEmail(u.email)
    setPassword(DEMO_PASSWORD)
    setError('')
    setShowHints(false)
  }

  return (
    <div className="login-page">
      <div className="login-brand">
        <span className="login-logo">🛍</span>
        <h1 className="login-title">TechNest</h1>
        <p className="login-tagline">Premium tech, delivered fast.</p>
      </div>

      <div className="login-card">
        <h2 className="login-card-heading">Sign in to your account</h2>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="field-group">
            <label className="field-label" htmlFor="email">Email</label>
            <input
              id="email"
              className="field-input"
              type="email"
              placeholder="you@example.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </div>

          <div className="field-group">
            <label className="field-label" htmlFor="password">Password</label>
            <input
              id="password"
              className="field-input"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button className="btn-primary btn-full btn-large" type="submit" disabled={!users.length}>
            {users.length ? 'Sign In' : 'Loading...'}
          </button>
        </form>

        <p className="auth-switch">
          Don't have an account? <Link to="/signup" className="auth-link">Create one</Link>
        </p>

        <div className="demo-hint-section">
          <button
            className="demo-hint-toggle"
            type="button"
            onClick={() => setShowHints(h => !h)}
          >
            {showHints ? '▲ Hide' : '▼ Show'} demo accounts
          </button>

          {showHints && (
            <div className="demo-accounts">
              {users.map(u => (
                <button key={u.id} className="demo-account-row" type="button" onClick={() => fillDemo(u)}>
                  <div className="demo-account-info">
                    <span className="demo-account-name">{u.name}</span>
                    <span className="demo-account-email">{u.email}</span>
                  </div>
                  <span className="demo-account-balance">{fmt(u.balance)} credits</span>
                </button>
              ))}
              <p className="demo-password-note">Password for all accounts: <code>{DEMO_PASSWORD}</code></p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
