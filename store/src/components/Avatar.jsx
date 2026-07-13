export default function Avatar({ name, size = 36 }) {
  const initials = name.split(' ').map(w => w[0]).join('').slice(0, 2)
  const hue = name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360
  return (
    <div
      className="avatar"
      style={{ width: size, height: size, background: `hsl(${hue},55%,32%)`, fontSize: size * 0.38 }}
    >
      {initials}
    </div>
  )
}
