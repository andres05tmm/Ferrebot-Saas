---
name: Sistema de Diseño Administrativo
colors:
  surface: '#f8f9fa'
  surface-dim: '#d9dadb'
  surface-bright: '#f8f9fa'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f4f5'
  surface-container: '#edeeef'
  surface-container-high: '#e7e8e9'
  surface-container-highest: '#e1e3e4'
  on-surface: '#191c1d'
  on-surface-variant: '#5c403b'
  inverse-surface: '#2e3132'
  inverse-on-surface: '#f0f1f2'
  outline: '#916f69'
  outline-variant: '#e5bdb6'
  surface-tint: '#bc1505'
  primary: '#a00a00'
  on-primary: '#ffffff'
  primary-container: '#c8200e'
  on-primary-container: '#ffded8'
  inverse-primary: '#ffb4a7'
  secondary: '#575e70'
  on-secondary: '#ffffff'
  secondary-container: '#d9dff5'
  on-secondary-container: '#5c6274'
  tertiary: '#454f5d'
  on-tertiary: '#ffffff'
  tertiary-container: '#5d6776'
  on-tertiary-container: '#dce6f7'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdad4'
  primary-fixed-dim: '#ffb4a7'
  on-primary-fixed: '#400200'
  on-primary-fixed-variant: '#910800'
  secondary-fixed: '#dce2f7'
  secondary-fixed-dim: '#c0c6db'
  on-secondary-fixed: '#141b2b'
  on-secondary-fixed-variant: '#404758'
  tertiary-fixed: '#d9e3f4'
  tertiary-fixed-dim: '#bdc7d8'
  on-tertiary-fixed: '#121c28'
  on-tertiary-fixed-variant: '#3e4755'
  background: '#f8f9fa'
  on-background: '#191c1d'
  surface-variant: '#e1e3e4'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
  headline-md-mobile:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  container-margin: 24px
  gutter: 16px
  padding-card: 20px
  stack-sm: 8px
  stack-md: 16px
---

## Brand & Style
This design system is engineered for high-utility SaaS environments focusing on appointment management. The brand personality is professional, authoritative, and highly efficient. The UI evokes a sense of organized control through a **Corporate/Modern** aesthetic, heavily influenced by functional minimalism. 

The visual narrative prioritizes clarity and speed of information processing. By utilizing a "clean-slate" approach with strategic bursts of the primary red, we ensure that user attention is directed toward critical actions and status changes without overwhelming the operator during long work sessions.

## Colors
The palette is rooted in a high-contrast functional logic.
- **Primary (#C8200E):** Reserved exclusively for primary calls-to-action (CTA), critical active states, and urgent notifications. It must be used sparingly to maintain its psychological impact.
- **Surface & Backgrounds:** We use #FFFFFF for primary containers and #F9FAFB for the main application background to create a subtle layered distinction.
- **Borders:** A consistent #E5E7EB is used for all structural divisions to maintain a soft but clear boundary.
- **Semantic Palette:** Status colors for appointments follow standard conventions: Amber (Pending), Emerald (Confirmed), Blue (Completed), Cool Gray (Cancelled), and Red (No-show).

## Typography
The system uses **Inter** for its exceptional legibility in data-dense interfaces. 
- **Hierarchy:** We use weight (SemiBold/Bold) rather than size to denote hierarchy in dashboard cards.
- **Contrast:** All text colors must meet WCAG AA standards against their respective backgrounds. Use #111827 for headings and #4B5563 for body text.
- **Labels:** Small labels use a slightly tighter letter spacing and increased weight to remain legible at 12px.
- **Localization:** All type scales are tested for Spanish language expansion (e.g., "Miércoles" vs "Wed").

## Layout & Spacing
The layout follows a **Fluid Grid** system with a focus on logical grouping.
- **Grid:** A 12-column grid is used for desktop views. Sidebar width is fixed at 280px to maximize the content area.
- **Rhythm:** We use a 4px base unit. Component internal padding should strictly follow increments of 4 (e.g., 8px, 12px, 16px, 20px).
- **Responsive Behavior:** On mobile, margins reduce to 16px, and 12-column layouts reflow into a single column. Information density is maintained by switching from horizontal table rows to vertical summary cards.

## Elevation & Depth
The system uses **Tonal Layers** and **Low-contrast Outlines** rather than heavy shadows to maintain a modern, flat appearance.
- **Level 0:** Main background (#F9FAFB).
- **Level 1:** Content cards and Sidebar (#FFFFFF). These feature a 1px border (#E5E7EB).
- **Level 2:** Modals and Popovers. These use a soft, diffused ambient shadow: `0 10px 15px -3px rgba(0, 0, 0, 0.1)`.
- **Active State:** Elements being dragged or interacted with receive a subtle primary-colored glow or a slightly darker border to indicate focus.

## Shapes
The shape language is **Soft (Level 1)**, reflecting professional precision. 
- **Standard Radius:** 0.25rem (4px) for input fields, checkboxes, and small buttons.
- **Large Radius:** 0.5rem (8px) for cards and main containers.
- **Pill:** Status badges (chips) use a fully rounded (9999px) radius to distinguish them from interactive buttons.

## Components
- **Buttons:** 
  - *Primary:* Background #C8200E, Text #FFFFFF. 
  - *Secondary:* Background #FFFFFF, Border #E5E7EB, Text #111827.
- **Status Badges (Estados):** 
  - Small text (label-sm), pill-shaped.
  - Styles use a 10% opacity background of the status color with 100% opacity text for high legibility (e.g., *Pendiente* is Amber).
- **Cards (Tarjetas):** White background, 1px border #E5E7EB. Headers should have a subtle bottom border to separate titles from content.
- **Forms:** 
  - Inputs have a height of 40px for desktop.
  - Labels are always positioned above the input (body-md, medium weight).
  - Focus state: Border #C8200E with a 2px outer ring of 10% primary red.
- **Lists & Tables:** 
  - Row height: 56px (Compact) or 72px (Standard).
  - Hover state: Background #F9FAFB to indicate interactivity.
- **Date Pickers:** Use primary red for the selected date and range highlight to ensure immediate visual confirmation of the chosen appointment window.