import './obrero.css'

// Escena "en obra": un obrerito con casco de oro martilla el letrero de
// melquiadez.com. La chispa de cada martillazo es el ánima dorada del sello.
// Todo es SVG + CSS (cero dependencias); la tinta usa currentColor y el oro
// usa --oro-vivo, así la escena sigue al tema claro/oscuro sola.
export default function Obrero({ className }) {
  return (
    <svg
      viewBox="0 0 560 440"
      role="img"
      aria-label="Un obrero con casco martillando el letrero de melquiadez.com"
      className={`obrero ${className ?? ''}`}
      fill="none"
    >
      {/* motas de oro flotando (el polvillo mágico del taller) */}
      <circle className="mota" cx="92" cy="84" r="3.5" />
      <circle className="mota mota-2" cx="500" cy="118" r="2.5" />
      <circle className="mota mota-3" cx="66" cy="286" r="3" />

      {/* suelo */}
      <line x1="28" y1="392" x2="532" y2="392" stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity=".3" />
      <circle cx="150" cy="388" r="2.5" fill="currentColor" opacity=".25" />
      <circle cx="490" cy="389" r="2" fill="currentColor" opacity=".25" />

      {/* postes del letrero */}
      <line x1="105" y1="205" x2="105" y2="392" stroke="currentColor" strokeWidth="5" strokeLinecap="round" opacity=".85" />
      <line x1="305" y1="205" x2="305" y2="392" stroke="currentColor" strokeWidth="5" strokeLinecap="round" opacity=".85" />

      {/* letrero: cuelga torcido del clavo izquierdo, se endereza con cada golpe */}
      <g className="letrero">
        <rect x="58" y="140" width="292" height="76" rx="8" className="tabla" stroke="currentColor" strokeWidth="3" />
        <line x1="76" y1="204" x2="150" y2="204" stroke="currentColor" strokeWidth="2" opacity=".12" />
        <line x1="230" y1="152" x2="330" y2="152" stroke="currentColor" strokeWidth="2" opacity=".12" />
        <text x="204" y="190" textAnchor="middle" className="letrero-texto">
          melquiadez.com
        </text>
        {/* clavo izquierdo, ya puesto */}
        <circle cx="80" cy="154" r="3.5" fill="currentColor" />
        {/* clavo derecho, el que recibe los martillazos */}
        <g className="clavo" stroke="currentColor" strokeLinecap="round">
          <line x1="328" y1="140" x2="328" y2="128" strokeWidth="3.5" />
          <line x1="321" y1="127" x2="335" y2="127" strokeWidth="4" />
        </g>
      </g>

      {/* chispa dorada del impacto (el ánima) */}
      <g className="chispa">
        <path d="M328 102 L332.5 115 L345 120 L332.5 125 L328 138 L323.5 125 L311 120 L323.5 115 Z" className="oro-fill" />
        <line x1="346" y1="102" x2="353" y2="95" className="oro-stroke" strokeWidth="3" strokeLinecap="round" />
        <line x1="310" y1="104" x2="303" y2="97" className="oro-stroke" strokeWidth="3" strokeLinecap="round" />
        <line x1="328" y1="94" x2="328" y2="85" className="oro-stroke" strokeWidth="3" strokeLinecap="round" />
      </g>

      {/* banquito */}
      <rect x="388" y="304" width="76" height="10" rx="3" className="madera" stroke="currentColor" strokeWidth="3" />
      <line x1="396" y1="314" x2="386" y2="390" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
      <line x1="456" y1="314" x2="466" y2="390" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
      <line x1="393" y1="352" x2="460" y2="352" stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity=".5" />

      {/* caja de herramientas */}
      <path d="M338 362 q10 -15 20 0" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <rect x="322" y="362" width="52" height="28" rx="5" className="madera" stroke="currentColor" strokeWidth="3" />
      <line x1="322" y1="372" x2="374" y2="372" stroke="currentColor" strokeWidth="2" opacity=".4" />

      {/* el obrerito */}
      <g className="personaje">
        {/* piernas y botas (quietas sobre el banquito) */}
        <line x1="414" y1="266" x2="408" y2="298" stroke="currentColor" strokeWidth="8" strokeLinecap="round" />
        <line x1="438" y1="266" x2="442" y2="298" stroke="currentColor" strokeWidth="8" strokeLinecap="round" />
        <rect x="396" y="294" width="22" height="11" rx="5" fill="currentColor" />
        <rect x="432" y="294" width="22" height="11" rx="5" fill="currentColor" />

        {/* brazo que sostiene la tabla */}
        <line x1="416" y1="224" x2="358" y2="206" stroke="currentColor" strokeWidth="7" strokeLinecap="round" />
        <circle cx="356" cy="205" r="6" className="piel" stroke="currentColor" strokeWidth="2.5" />

        {/* overol */}
        <rect x="400" y="210" width="52" height="62" rx="14" className="overol" stroke="currentColor" strokeWidth="3" />
        <line x1="412" y1="238" x2="440" y2="238" stroke="currentColor" strokeWidth="2" opacity=".35" />

        {/* cabeza */}
        <circle cx="424" cy="184" r="25" className="piel" stroke="currentColor" strokeWidth="3" />
        <circle className="ojo" cx="410" cy="182" r="3.2" fill="currentColor" />
        <path d="M402 193 q6 5 12 2.5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />

        {/* casco de oro */}
        <path d="M398 174 Q398 148 424 148 Q450 148 450 174 Z" className="oro-fill" stroke="currentColor" strokeWidth="3" />
        <rect x="416" y="142" width="16" height="7" rx="3.5" className="oro-fill" stroke="currentColor" strokeWidth="2.5" />
        <rect x="388" y="171" width="72" height="8" rx="4" className="oro-fill" stroke="currentColor" strokeWidth="2.5" />

        {/* brazo del martillo: dibujado en pose de golpe, la animación lo levanta */}
        <g className="brazo-martillo">
          <line x1="428" y1="222" x2="392" y2="158" stroke="currentColor" strokeWidth="7" strokeLinecap="round" />
          <line x1="392" y1="158" x2="337" y2="119" stroke="currentColor" strokeWidth="5" strokeLinecap="round" />
          <line x1="328" y1="107" x2="328" y2="126" stroke="currentColor" strokeWidth="19" strokeLinecap="square" />
          <circle cx="392" cy="158" r="6.5" className="piel" stroke="currentColor" strokeWidth="2.5" />
        </g>
      </g>
    </svg>
  )
}
