import { Outlet, Navigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import Header from './Header'

export default function Layout() {
  const { activeUser } = useApp()
  if (!activeUser) return <Navigate to="/" replace />
  return (
    <>
      <Header />
      <main className="main-content">
        <Outlet />
      </main>
    </>
  )
}
