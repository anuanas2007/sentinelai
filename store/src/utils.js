export const API = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export const ITEM_EMOJI = {
  headphones: '🎧',
  keyboard:   '⌨️',
  usb_hub:    '🔌',
  webcam:     '📷',
  mouse_pad:  '🖱️',
  desk_lamp:  '💡',
  ssd:        '💾',
}

export const ITEM_CATEGORY = {
  headphones: 'Audio',
  keyboard:   'Peripherals',
  usb_hub:    'Accessories',
  webcam:     'Video',
  mouse_pad:  'Peripherals',
  desk_lamp:  'Lighting',
  ssd:        'Storage',
}

export const fmt = n => `$${Number(n).toFixed(2)}`

export function stockLabel(stock) {
  if (stock === 0) return { text: 'Out of stock', cls: 'stock-out' }
  if (stock <= 3)  return { text: `Only ${stock} left`, cls: 'stock-low' }
  return { text: 'In stock', cls: 'stock-ok' }
}
