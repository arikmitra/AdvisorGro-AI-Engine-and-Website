---
name: Obsidian Electric
colors:
  surface: '#121414'
  surface-dim: '#121414'
  surface-bright: '#383939'
  surface-container-lowest: '#0d0e0f'
  surface-container-low: '#1b1c1c'
  surface-container: '#1f2020'
  surface-container-high: '#292a2a'
  surface-container-highest: '#343535'
  on-surface: '#e3e2e2'
  on-surface-variant: '#c3c6d7'
  inverse-surface: '#e3e2e2'
  inverse-on-surface: '#2f3031'
  outline: '#8d90a0'
  outline-variant: '#424655'
  surface-tint: '#b3c5ff'
  primary: '#b3c5ff'
  on-primary: '#002b75'
  primary-container: '#5e8bff'
  on-primary-container: '#002567'
  inverse-primary: '#0054d6'
  secondary: '#fabc3e'
  on-secondary: '#422d00'
  secondary-container: '#c38c00'
  on-secondary-container: '#3f2b00'
  tertiary: '#ffb4a5'
  on-tertiary: '#650a00'
  tertiary-container: '#f55f44'
  on-tertiary-container: '#590800'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#dae1ff'
  primary-fixed-dim: '#b3c5ff'
  on-primary-fixed: '#001849'
  on-primary-fixed-variant: '#003fa4'
  secondary-fixed: '#ffdea8'
  secondary-fixed-dim: '#fabc3e'
  on-secondary-fixed: '#271900'
  on-secondary-fixed-variant: '#5e4200'
  tertiary-fixed: '#ffdad3'
  tertiary-fixed-dim: '#ffb4a5'
  on-tertiary-fixed: '#3f0400'
  on-tertiary-fixed-variant: '#8e1401'
  background: '#121414'
  on-background: '#e3e2e2'
  surface-variant: '#343535'
typography:
  display-hero:
    fontFamily: Bricolage Grotesque
    fontSize: 56px
    fontWeight: '700'
    lineHeight: 64px
    letterSpacing: -0.03em
  display-hero-mobile:
    fontFamily: Bricolage Grotesque
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Bricolage Grotesque
    fontSize: 36px
    fontWeight: '600'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Bricolage Grotesque
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Bricolage Grotesque
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Bricolage Grotesque
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  editorial-callout:
    fontFamily: Source Serif 4
    fontSize: 22px
    fontWeight: '400'
    lineHeight: 32px
    letterSpacing: -0.005em
  editorial-quote:
    fontFamily: Source Serif 4
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 26px
    letterSpacing: 0em
  body-lg:
    fontFamily: Bricolage Grotesque
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: -0.01em
  body-md:
    fontFamily: Bricolage Grotesque
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0em
  body-sm:
    fontFamily: Bricolage Grotesque
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0.005em
  label-md:
    fontFamily: Bricolage Grotesque
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: Bricolage Grotesque
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
  code-snippet:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-sm: 1rem
  margin: 2rem
  margin-sm: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.5rem
---

## Brand & Style

The design system projects a high-focus, technical, and hyper-modern environment engineered for high-agency operators, engineers, and digital craftspeople. The aesthetic merges technical minimalism with high-contrast editorial accents: deep obsidian planes, surgical 1px boundaries, stark luminous type, and electric blue surges.

Key brand attributes:
- **Disciplined Precision**: Interfaces are dense, intentional, and strictly ordered. Whitespace is deliberate and atmospheric.
- **Electric Utility**: High-saturation accents pierce through deep dark layers, communicating instantaneous feedback and critical status.
- **Technical Humanism**: The marriage of an eccentric modern grotesque with an exacting transitional serif introduces intellectual authority without sacrificing developer-grade utility.

## Colors

The palette operates on high-contrast values built over void-like obsidian tones. Pure blacks and near-black tonal surfaces ground the system, while stark white typography and vibrant spectral accents command visual weight.

### Surface System
- **Canvas Base**: `#0a0a0a` (pure deep obsidian, root canvas).
- **Surface Layer 1**: `#141414` (cards, side panels, nested containers).
- **Surface Layer 2**: `#1c1c1c` (modal overlays, popovers, active selection states).
- **Surface Interactive**: `#242424` (hover states on neutral surfaces).

### Boundaries & Outlines
- **Subtle Stroke**: `#2b2b2b` (default 1px container and divider border).
- **Focused Stroke**: `#404040` (elevated structure, table row dividers, inactive inputs).
- **Active Stroke**: `#3a77ff` (focus rings, active selection states).

### Typography & Content
- **Text Primary**: `#f4f4f4` (headlines, critical data points, active states).
- **Text Secondary**: `#8e8e8e` (body context, secondary metadata, unselected nav labels).
- **Text Tertiary**: `#575757` (disabled elements, placeholder text, breadcrumb dividers).

### Accents & Status
- **Primary Electric Blue**: `#3a77ff` (call-to-actions, active indicators, brand markers).
- **Primary Electric Blue Hover**: `#6193ff`.
- **Caution Amber**: `#e0a526` (warnings, pending states, cautionary flags).
- **Warning Coral Red**: `#ee5a3f` (errors, destructive actions, emergency alerts).

## Typography

The type architecture pairs the energetic, idiosyncratic geometry of **Bricolage Grotesque** with the classic cadence of **Source Serif 4**.

- **Bricolage Grotesque** is deployed across headers, primary narrative paths, UI controls, navigation, and metrics. Tight letter-spacing on display tokens preserves a compact, architectural presence.
- **Source Serif 4** provides distinct editorial accents: contextual callouts, quotes, long-form footnotes, and emphasized narrative anchors. It injects deliberate human warmth into an otherwise austere dark canvas.
- Maintain high contrast: `display` and `headline` levels use `#f4f4f4`; metadata, subtext, and supporting labels sit at `#8e8e8e`.

## Layout & Spacing

The layout model is anchored by a strict 12-column responsive fluid grid that collapses to 8 columns on tablet devices and 4 columns on mobile viewports.

### Breakpoints
- **Mobile (`< 768px`)**: 4-column layout, `margin-sm` (1rem), `gutter-sm` (1rem). Navigation docks to the bottom edge or compact drawer.
- **Tablet (`768px - 1024px`)**: 8-column layout, `margin` (2rem), `gutter` (1.5rem). Secondary toolbars collapse into dropdown segments.
- **Desktop (`> 1024px`)**: 12-column layout, max-width constrained to `1440px` with centered canvas margins. Persistent utility sidebars span 2 to 3 columns.

### Spatial Discipline
Distances follow a predictable rhythm derived from a 4px/8px baseline. Use `space-xs` and `space-sm` for strictly related internal pairings (such as icon-to-label gaps or metadata clusters). Use `space-md` for standard card padding and `space-xl` to segment primary modular sections.

## Elevation & Depth

This design system avoids blurry drop-shadows and skeuomorphic gradients in favor of **structural planar elevation** and **low-contrast outline separation**.

1. **Planar Tiers (Depth via Tone)**:
   - Level 0 (Ground Canvas): `#0a0a0a`.
   - Level 1 (Card & Module Layer): `#141414` bordered with 1px `#2b2b2b`.
   - Level 2 (Floating Popovers, Dropdowns, Sheets): `#1c1c1c` bordered with 1px `#404040`.
2. **Sharp Borders Over Soft Shadows**: Depth is established via crisp, 1px solid boundaries. Diffuse shadows are absent, except for an optional faint electric blue atmospheric bloom (`0 0 24px rgba(58, 119, 255, 0.15)`) applied strictly to primary interactive targets in focused states.
3. **Glass Substrates**: Modals and high-level overlays leverage translucent backs (`rgba(20, 20, 20, 0.8)`) backed by a heavy `backdrop-filter: blur(16px)` to blur background planes without sacrificing technical minimalism.

## Shapes

The design system adopts a **Soft (0.25rem / 4px)** baseline geometry. Surfaces project an industrial, precision-milled visual language. 

- **Small Components (Buttons, Chips, Inputs)**: `0.25rem` (4px). Clean, crisp corners reinforce the technical feel.
- **Medium Panels (Cards, Drawers, Modals)**: `0.5rem` (8px). Softened just enough to avoid pure brutality while maintaining structural discipline.
- **Indicator Dots & Avatars**: True circular styling (`rounded-full`) is reserved exclusively for user avatars, status pills, and toggle thumb nodes to provide geometric contrast against architectural grids.

## Components

### Buttons
- **Primary**: Background `#3a77ff`, text `#f4f4f4`, font `label-md`. Hover shifts background to `#6193ff`. Active/pressed uses `#295ecc`. Border radius `0.25rem`. No border.
- **Secondary**: Background `#141414`, border 1px solid `#2b2b2b`, text `#f4f4f4`. Hover elevates background to `#1c1c1c` and border to `#404040`.
- **Destructive**: Background transparent, border 1px solid `#ee5a3f`, text `#ee5a3f`. Hover background `rgba(238, 90, 63, 0.1)`.
- **Ghost**: Background transparent, text `#8e8e8e`. Hover background `#141414`, text `#f4f4f4`.

### Input Fields
- Base background `#0a0a0a`, border 1px solid `#2b2b2b`, text `#f4f4f4`, font `body-md`. Padding `0.5rem 0.75rem`.
- **Focus**: Border switches cleanly to `#3a77ff`, accompanied by a 1px pseudo-outline (no soft glow).
- **Error**: Border turns to `#ee5a3f`, helper text rendered in `#ee5a3f` using `body-sm`.
- **Placeholder**: Text color `#575757`.

### Cards & Panels
- Surfaces use `#141414` outlined by 1px `#2b2b2b`. Corner radius `0.5rem`.
- Padding set to `1.5rem` (`space-lg`).
- Interactive cards transition border color to `#404040` on hover, with title shifting from `#8e8e8e` to `#f4f4f4`.

### Chips & Badges
- **Status (Caution)**: Background `rgba(224, 165, 38, 0.12)`, border 1px solid `rgba(224, 165, 38, 0.35)`, text `#e0a526`. Font `label-sm`.
- **Status (Warning)**: Background `rgba(238, 90, 63, 0.12)`, border 1px solid `rgba(238, 90, 63, 0.35)`, text `#ee5a3f`. Font `label-sm`.
- **Interactive / Filter Chip**: Background `#141414`, border 1px solid `#2b2b2b`, text `#8e8e8e`. Selected state: background `#1c1c1c`, border `#3a77ff`, text `#f4f4f4`.

### Checkboxes & Radios
- Box/Circle dimension `16px x 16px`, background `#0a0a0a`, border 1px solid `#404040`. Radius `2px` for checkbox, circular for radio.
- **Checked**: Background `#3a77ff`, border `#3a77ff`. The interior mark is crisp white `#f4f4f4`.

### Lists & Data Tables
- Row dividers use a crisp 1px solid `#2b2b2b`.
- Headers use font `label-sm`, all caps, tracking wide, color `#8e8e8e`.
- Row hover triggers background tint `#141414` across the entire row without vertical jump or dimension shifts.

### Editorial Callouts (System Distinctive)
- Left-bordered container with a 2px stroke of `#3a77ff` on a `#141414` background.
- Employs **Source Serif 4** (`editorial-callout`) for introspective, long-form insights, commentary, or human-authored release notes embedded within technical flows.