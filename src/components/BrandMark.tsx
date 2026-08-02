/**
 * The Caudal mark, as SVG.
 *
 * Geometry is the same as `tools/icon.py` in normalised coordinates, so the
 * header and the app icon are the same drawing rather than two things that
 * merely resemble each other. If one changes, change both.
 *
 * Each strand leaves the source vertically and arrives at its tip vertically,
 * which is what keeps the curve free of any visible corner.
 */
const SOURCE = { x: 50, y: 81.5 }
const TOP = 18.5
const STROKE = 5.2

const STRANDS = [
  { x: 21.2, colour: '#4a9eff' },
  { x: 50.0, colour: '#56c8e6' },
  { x: 78.8, colour: '#7c84f5' },
]

export function BrandMark({ size = 18 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      aria-hidden="true"
    >
      {STRANDS.map((s) => (
        <g key={s.x} stroke={s.colour} fill={s.colour}>
          <path
            d={`M ${SOURCE.x} ${SOURCE.y} C ${SOURCE.x} ${SOURCE.y - 30}, ${s.x} ${TOP + 30}, ${s.x} ${TOP}`}
            strokeWidth={STROKE}
            strokeLinecap="round"
            fill="none"
          />
          <circle cx={s.x} cy={TOP} r={STROKE * 0.92} stroke="none" />
        </g>
      ))}
      <circle cx={SOURCE.x} cy={SOURCE.y} r={7.8} fill="#f0f5fb" />
    </svg>
  )
}
