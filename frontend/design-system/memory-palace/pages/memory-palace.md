# Memory Palace Page Overrides

> **PROJECT:** Memory Palace
> **Generated:** 2026-02-15 15:29:26
> **Page Type:** General

> ⚠️ **IMPORTANT:** Rules in this file **override** the Master file (`design-system/MASTER.md`).
> Only deviations from the Master are documented here. For all other rules, refer to the Master.

---

## Page-Specific Rules

### Layout Overrides

- **Max Width:** 1280px
- **Layout:** Two-column workbench (`340px / fluid`) with sticky top navigation
- **Information Architecture:** `Conversation Vault` + `Child Filters` (left) / `Node Workspace` (right)

### Spacing Overrides

- Use larger vertical rhythm for readability:
  - `--space-lg` for card internals
  - `--space-xl` between major sections

### Typography Overrides

- Keep Master hierarchy; increase body readability:
  - body line-height `1.65`
  - code/pre line-height `1.75`

### Color Overrides

Use **Porcelain & Sand** palette for this page:

| Role | Hex | Intent |
|------|-----|--------|
| Canvas | `#F6F2EA` | Porcelain background |
| Surface | `#FDFAF6` | Card surface |
| Sand | `#E7DBC8` | Soft elevation / subtle chips |
| Sand Deep | `#D6C1A3` | Depth accents |
| Ink | `#2F2A24` | Primary text |
| Muted Ink | `#7F6F5D` | Secondary text |
| Accent | `#B3854F` | Primary CTA / focus |
| Accent Deep | `#8F6A45` | Hover / strong borders |

### Component Overrides

- Buttons:
  - Primary: solid `Accent`
  - Secondary: white surface + `Sand` border
- Cards:
  - Radius `16px`
  - Shadow: warm, low-contrast (`rgba(86,66,44,0.08~0.12)`)
- Inputs:
  - White surface
  - Border `#DFD1BF`
  - Focus ring from `Accent` at 20% alpha

---

## Page-Specific Components

- `Conversation Vault` (textarea + title + metadata + commit action)
- `Breadcrumb Path Bar` (fast hierarchy jump)
- `Child Memory Grid` (search + priority filter driven)
- `Node Workspace` (edit, save, delete path)

---

## Recommendations

- Effects:
  - subtle paper grid texture (very low opacity)
  - hover lift only (`translateY(-2px)` max)
  - no heavy blur, no neon glow
