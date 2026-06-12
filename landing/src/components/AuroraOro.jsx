import { useEffect, useRef } from 'react'

/*
 * AuroraOro — el shader de `aurora-flow` (21st.dev, Scottclayton3d) retintado a la
 * paleta Melquiadez (oro viejo sobre tinta / vetas doradas sobre papel) y portado a
 * WebGL puro: el original montaba three + fiber solo para dibujar un plano con este
 * mismo fragment shader; sin three el chunk pasa de ~250KB a ~3KB.
 *
 * Sutileza por contrato (regla del plan): un solo shader por viewport, opacidad baja
 * y máscara que lo funde con el fondo. Con prefers-reduced-motion queda el primer
 * frame estático. uTema interpola la paleta clara/oscura.
 */

const VERT = `
attribute vec2 p;
void main() { gl_Position = vec4(p, 0.0, 1.0); }
`

const FRAG = `
precision mediump float;
uniform float time;
uniform vec2 resolution;
uniform float uTema; // 0 = claro (papel), 1 = oscuro (tinta)

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec2 mod289(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec3 permute(vec3 x) { return mod289(((x*34.0)+1.0)*x); }

float snoise(vec2 v) {
  const vec4 C = vec4(0.211324865405187, 0.366025403784439,
                     -0.577350269189626, 0.024390243902439);
  vec2 i  = floor(v + dot(v, C.yy) );
  vec2 x0 = v -   i + dot(i, C.xx);
  vec2 i1;
  i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
  vec4 x12 = x0.xyxy + C.xxzz;
  x12.xy -= i1;
  i = mod289(i);
  vec3 p = permute( permute( i.y + vec3(0.0, i1.y, 1.0 ))
    + i.x + vec3(0.0, i1.x, 1.0 ));
  vec3 m = max(0.5 - vec3(dot(x0,x0), dot(x12.xy,x12.xy), dot(x12.zw,x12.zw)), 0.0);
  m = m*m;
  m = m*m;
  vec3 x = 2.0 * fract(p * C.www) - 1.0;
  vec3 h = abs(x) - 0.5;
  vec3 ox = floor(x + 0.5);
  vec3 a0 = x - ox;
  m *= 1.79284291400159 - 0.85373472095314 * ( a0*a0 + h*h );
  vec3 g;
  g.x  = a0.x  * x0.x  + h.x  * x0.y;
  g.yz = a0.yz * x12.xz + h.yz * x12.yw;
  return 130.0 * dot(m, g);
}

void main() {
  vec2 uv = gl_FragCoord.xy / resolution;

  // flujos de aurora (idénticos al original, ritmo más calmo)
  float flow1 = snoise(vec2(uv.x * 2.0 + time * 0.07, uv.y * 0.5 + time * 0.035));
  float flow2 = snoise(vec2(uv.x * 1.5 + time * 0.055, uv.y * 0.8 + time * 0.02));
  float flow3 = snoise(vec2(uv.x * 3.0 + time * 0.085, uv.y * 0.3 + time * 0.05));

  float streaks = sin((uv.x + flow1 * 0.3) * 8.0 + time * 0.14) * 0.5 + 0.5;
  streaks *= sin((uv.y + flow2 * 0.2) * 12.0 + time * 0.1) * 0.5 + 0.5;

  float aurora = (flow1 + flow2 + flow3) * 0.33 + 0.5;
  aurora = pow(aurora, 2.0);

  // paleta noche: tinta → bronce → oro viejo → oro claro
  vec3 baseOsc   = vec3(0.070, 0.047, 0.031);
  vec3 bronceOsc = vec3(0.240, 0.165, 0.066);
  vec3 oroOsc    = vec3(0.772, 0.584, 0.231);
  vec3 brilloOsc = vec3(0.855, 0.690, 0.376);

  // paleta día: papel → arena → oro suave (vetas, no manchas)
  vec3 baseCla   = vec3(0.976, 0.968, 0.953);
  vec3 bronceCla = vec3(0.930, 0.880, 0.780);
  vec3 oroCla    = vec3(0.840, 0.720, 0.480);
  vec3 brilloCla = vec3(0.772, 0.584, 0.231);

  vec3 base   = mix(baseCla,   baseOsc,   uTema);
  vec3 bronce = mix(bronceCla, bronceOsc, uTema);
  vec3 oro    = mix(oroCla,    oroOsc,    uTema);
  vec3 brillo = mix(brilloCla, brilloOsc, uTema);

  vec3 color = base;
  float f1 = smoothstep(0.3, 0.7, aurora + streaks * 0.3);
  color = mix(color, bronce, f1);
  float f2 = smoothstep(0.6, 0.9, aurora + flow1 * 0.4);
  color = mix(color, oro, f2 * 0.8);
  float f3 = smoothstep(0.8, 1.0, streaks + aurora * 0.5);
  color = mix(color, brillo, f3 * 0.55);

  float grano = snoise(uv * 100.0) * 0.015;
  color += grano;

  gl_FragColor = vec4(color, 1.0);
}
`

export default function AuroraOro({ tema = 'oscuro', className = '', intensidad = 1 }) {
  const ref = useRef(null)
  const uTemaRef = useRef(tema === 'oscuro' ? 1 : 0)
  uTemaRef.current = tema === 'oscuro' ? 1 : 0

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return undefined
    // preserveDrawingBuffer: el fondo se redibuja poco (rAF lento o frame único con
    // reduced-motion); preservar el buffer evita frames negros al compositar.
    const opciones = { antialias: false, alpha: true, preserveDrawingBuffer: true }
    const gl = canvas.getContext('webgl2', opciones) || canvas.getContext('webgl', opciones)
    if (!gl) return undefined // sin WebGL: queda el fondo plano del CSS

    function compilar(tipo, src) {
      const s = gl.createShader(tipo)
      gl.shaderSource(s, src)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        console.error('AuroraOro shader:', gl.getShaderInfoLog(s))
      }
      return s
    }
    const prog = gl.createProgram()
    gl.attachShader(prog, compilar(gl.VERTEX_SHADER, VERT))
    gl.attachShader(prog, compilar(gl.FRAGMENT_SHADER, FRAG))
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error('AuroraOro link:', gl.getProgramInfoLog(prog))
      return undefined
    }
    gl.useProgram(prog)

    // triángulo que cubre la pantalla
    const buf = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const locP = gl.getAttribLocation(prog, 'p')
    gl.enableVertexAttribArray(locP)
    gl.vertexAttribPointer(locP, 2, gl.FLOAT, false, 0, 0)

    const locTime = gl.getUniformLocation(prog, 'time')
    const locRes = gl.getUniformLocation(prog, 'resolution')
    const locTema = gl.getUniformLocation(prog, 'uTema')

    // resolución modesta a propósito: es un fondo difuso, no necesita DPR completo
    const dpr = Math.min(window.devicePixelRatio || 1, 1.25)
    function redimensionar() {
      const w = Math.max(1, Math.floor(canvas.clientWidth * dpr * 0.66))
      const h = Math.max(1, Math.floor(canvas.clientHeight * dpr * 0.66))
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
        gl.viewport(0, 0, w, h)
      }
    }
    redimensionar()
    const obs = new ResizeObserver(redimensionar)
    obs.observe(canvas)

    const reducido = matchMedia('(prefers-reduced-motion: reduce)').matches
    let rafId = 0
    let t = 0
    function pintar() {
      gl.uniform1f(locTime, t)
      gl.uniform2f(locRes, canvas.width, canvas.height)
      gl.uniform1f(locTema, uTemaRef.current)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }
    function ciclo() {
      t += 0.016
      pintar()
      rafId = requestAnimationFrame(ciclo)
    }
    pintar()
    if (!reducido) rafId = requestAnimationFrame(ciclo)

    return () => {
      cancelAnimationFrame(rafId)
      obs.disconnect()
      gl.deleteBuffer(buf)
      gl.deleteProgram(prog)
    }
  }, [])

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      className={className}
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        opacity: intensidad,
        pointerEvents: 'none',
      }}
    />
  )
}
