import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { API } from '../utils'

export default function SignupPage() {
  const { setActiveUser } = useApp()
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${API}/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password }),
      })
      if (res.status === 409) {
        setError('An account with that email already exists.')
        setLoading(false)
        return
      }
      if (!res.ok) {
        setError('Something went wrong. Please try again.')
        setLoading(false)
        return
      }
      const user = await res.json()
      sessionStorage.setItem(`pw:${user.id}`, password)
      setActiveUser(user)
      navigate('/shop')
    } catch {
      setError('Network error. Please try again.')
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-brand">
        <span className="login-logo">🛍</span>
        <h1 className="login-title">TechNest</h1>
        <p className="login-tagline">Create your account</p>
      </div>

      <div className="login-card">
        <h2 className="login-card-heading">Sign up</h2>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="field-group">
            <label className="field-label" htmlFor="name">Full Name</label>
            <input
              id="name"
              className="field-input"
              type="text"
              placeholder="Jane Smith"
              value={name}
              onChange={e => setName(e.target.value)}
              required
              minLength={2}
            />
          </div>

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
              required
              minLength={6}
            />
          </div>

          {error && <div className="login-error">{error}</div>}

          <button className="btn-primary btn-full btn-large" type="submit" disabled={loading}>
            {loading ? 'Creating account...' : 'Create Account'}
          </button>
        </form>

        <div className="signup-welcome-note">
          🎉 New accounts start with <strong>$200.00</strong> in store credits.
        </div>

        <p className="auth-switch">
          Already have an account? <Link to="/" className="auth-link">Sign in</Link>
        </p>
      </div>
    </div>
  )
}
