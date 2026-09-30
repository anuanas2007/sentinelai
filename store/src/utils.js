export const API       = import.meta.env.VITE_API_BASE   || 'http://localhost:8000'
export const AGENT_API = import.meta.env.VITE_AGENT_BASE || 'http://localhost:9000'

// Unsplash photos — royalty-free, credited in README
export const ITEM_IMAGE = {
  headphones:       'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=400&q=80',
  keyboard:         'https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?w=400&q=80',
  usb_hub:          'https://images.unsplash.com/photo-1625314897518-bb4fe6e95229?w=400&q=80',
  webcam:           'https://images.unsplash.com/photo-1587826080692-f439cd0b70da?w=400&q=80',
  mouse_pad:        'https://images.unsplash.com/photo-1616763355603-9755a640a287?w=400&q=80',
  desk_lamp:        'https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=400&q=80',
  ssd:              'https://images.unsplash.com/photo-1597138804456-e7dca7f59d54?w=400&q=80',
  monitor:          'https://images.unsplash.com/photo-1547119957-637f8679db1e?w=400&q=80',
  laptop_stand:     'https://images.unsplash.com/photo-1611186871525-9b6f4b52b3a6?w=400&q=80',
  wireless_charger: 'https://images.unsplash.com/photo-1583863788434-e58a36330cf0?w=400&q=80',
  microphone:       'https://images.unsplash.com/photo-1598550476439-6847785fcea6?w=400&q=80',
  led_strip:        'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=400&q=80',
  controller:       'https://images.unsplash.com/photo-1593118247619-e2d6f056869e?w=400&q=80',
  cable_pack:       'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=400&q=80',
  headphone_stand:  'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=400&q=80',
}

export const ITEM_CATEGORY = {
  headphones:       'Audio',
  keyboard:         'Peripherals',
  usb_hub:          'Accessories',
  webcam:           'Video',
  mouse_pad:        'Peripherals',
  desk_lamp:        'Lighting',
  ssd:              'Storage',
  monitor:          'Displays',
  laptop_stand:     'Accessories',
  wireless_charger: 'Charging',
  microphone:       'Audio',
  led_strip:        'Lighting',
  controller:       'Gaming',
  cable_pack:       'Accessories',
  headphone_stand:  'Audio',
}

export const fmt = n => `$${Number(n).toFixed(2)}`

export function stockLabel(stock) {
  if (stock === 0) return { text: 'Out of stock', cls: 'stock-out' }
  if (stock <= 3)  return { text: `Only ${stock} left`, cls: 'stock-low' }
  return { text: 'In stock', cls: 'stock-ok' }
}
