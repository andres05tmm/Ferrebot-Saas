/*
 * registerSw.js — registro del service worker de la PWA.
 *
 * AISLADO a propósito: importa el módulo virtual `virtual:pwa-register` (solo existe bajo Vite,
 * no bajo vitest), por eso NINGÚN componente testeado lo importa. Se llama una sola vez desde main.jsx.
 * `autoUpdate` + `immediate`: al desplegar una versión nueva, el SW la toma y refresca en el próximo load.
 */
import { registerSW } from 'virtual:pwa-register'

export function registerServiceWorker() {
  // En dev no hay SW (devOptions.enabled=false): evita cachear durante el desarrollo.
  if (import.meta.env.DEV) return
  registerSW({ immediate: true })
}
