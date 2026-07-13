import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { API } from '../utils'

const AppContext = createContext(null)

export function AppProvider({ children }) {
  const [users, setUsers]           = useState([])
  const [items, setItems]           = useState([])
  const [activeUser, setActiveUser] = useState(null)
  const [cart, setCart]             = useState({}) // { item_name: qty }
  const [lastOrder, setLastOrder]   = useState(null)

  useEffect(() => {
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
    fetch(`${API}/users`).then(r => r.json()).then(setUsers).catch(() => {})
  }, [])

  const refreshData = useCallback(() => {
    fetch(`${API}/users`).then(r => r.json()).then(data => {
      setUsers(data)
      setActiveUser(u => u ? (data.find(d => d.id === u.id) ?? u) : u)
    }).catch(() => {})
    fetch(`${API}/items`).then(r => r.json()).then(setItems).catch(() => {})
  }, [])

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

  function clearCart() { setCart({}) }

  const cartCount = Object.values(cart).reduce((s, n) => s + n, 0)

  return (
    <AppContext.Provider value={{
      users, items, activeUser, setActiveUser,
      cart, cartCount, addToCart, changeQty, removeFromCart, clearCart,
      lastOrder, setLastOrder, refreshData,
    }}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
