// Map icons drawn after the game's silhouettes (garrison flag, outpost, airhead parachute), in a 24×24
// view box, filled and stroked with the team colour. A dark halo keeps them readable on the map.

const HALO = "drop-shadow(0 0 1px rgba(0, 0, 0, 0.9))";

const SHAPES = {
  garrison: (color) => (
    <>
      <line x1="9" y1="2.5" x2="9" y2="15" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <path d="M9 2.5 L20 6.5 L9 10.5 Z" fill={color} />
      <circle cx="9" cy="18.5" r="3.2" fill="none" stroke={color} strokeWidth="2" />
    </>
  ),
  outpost: (color) => (
    <>
      <line x1="12" y1="2" x2="12" y2="12" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <path d="M12 12 L19 21.5 L5 21.5 Z" fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
    </>
  ),
  airhead: (color) => (
    <>
      <path d="M3.5 9.5 A8.5 7 0 0 1 20.5 9.5 Z" fill={color} />
      <path
        d="M4 9.5 L12 15 L20 9.5 M9 9.5 L12 15 L15 9.5"
        fill="none"
        stroke={color}
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      <path d="M12 15 L17 22 L7 22 Z" fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
    </>
  ),
};

/** Icon of ``kind`` centred on (0, 0), ``size`` wide in the current SVG units. */
export function GameIcon({ kind, color, size }) {
  const shape = SHAPES[kind];
  if (!shape) return null;
  return (
    <g transform={`scale(${size / 24}) translate(-12 -12)`} style={{ filter: HALO }}>
      {/* Invisible square: the whole icon area catches the pointer for the tooltip. */}
      <rect width="24" height="24" fill="transparent" />
      {shape(color)}
    </g>
  );
}
