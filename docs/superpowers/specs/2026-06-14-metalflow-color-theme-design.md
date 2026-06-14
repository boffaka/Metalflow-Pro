# MetalFlow Pro Color Theme Design

## Objective

Apply the user-provided color and typography system exactly across MetalFlow Pro. The application remains dark by default. The light drafting-table treatment is isolated to elements explicitly carrying the `.pdc-canvas` class.

## Global Theme

- Set `color-scheme: dark` at the document level.
- Keep Tailwind `darkMode: "class"`.
- Apply the `dark` class by default at application startup.
- Use Inter Variable for the interface and IBM Plex Mono for technical values, identifiers, tables, and numeric data.
- Do not use gold as the default body-text color. Body text must remain neutral and highly legible against dark surfaces.

## Global Palette

The following values are canonical:

| Token | Value | Usage |
|---|---|---|
| `gold-400` | `#FCD34D` | bright highlights |
| `gold-500` | `#F59E0B` | primary accent |
| `gold-600` | `#D97706` | pressed and strong states |
| `teal-400` | `#2DD4BF` | bright secondary accent |
| `teal-500` | `#0D9488` | secondary accent |
| `teal-600` | `#0F766E` | pressed and strong states |
| `surface-900` | `#0B0F1A` | application background |
| `surface-800` | `#111827` | primary panels |
| `surface-700` | `#1A2235` | cards and inputs |
| `surface-600` | `#222D42` | raised and hover surfaces |

Existing application variables must map to these canonical values. Text colors may use neutral light values chosen for WCAG-readable contrast, but must not alter the supplied accent and surface values.

The scrollbar thumb is `#2A3A54` on a transparent track.

## Typography

- Install and import `@fontsource-variable/inter`.
- Install and import `@fontsource/ibm-plex-mono`.
- Install and import `@fontsource/caveat`.
- `Inter Variable` is the global sans-serif font.
- `IBM Plex Mono` is the global monospace font.
- `Caveat` is permitted only for annotation elements inside `.pdc-canvas`.

System-font fallbacks remain available when font assets fail to load.

## PDC Living Circuit

The following values apply only inside `.pdc-canvas`:

| Token | Value |
|---|---|
| `pdc-canvas` | `#F5EFE2` |
| `pdc-ink` | `#1A2330` |
| `pdc-ink-soft` | `#5D6878` |
| `pdc-gold` | `#C9A24A` |
| `pdc-gold-hot` | `#FFD063` |
| `pdc-alert` | `#C24545` |

`.pdc-canvas` establishes a local light color scheme, local CSS variables, paper background, dark ink, and readable controls. Global dark-theme selectors must not override these local values.

Only elements explicitly assigned `.pdc-canvas` receive this treatment. Existing flowsheet or simulation canvases are not automatically converted based on their current class names.

## Data Source Washes

Inside `.pdc-canvas`, provenance classes map exactly as follows:

| Class | Background |
|---|---|
| `.pdc-source-lims` | `rgba(63, 111, 168, 0.12)` |
| `.pdc-source-calculated` | `rgba(126, 91, 168, 0.12)` |
| `.pdc-source-manual` | `rgba(184, 106, 42, 0.10)` |
| `.pdc-source-project` | `rgba(181, 138, 46, 0.12)` |
| `.pdc-source-design` | `rgba(140, 124, 104, 0.08)` |
| `.pdc-source-default` | `rgba(168, 154, 130, 0.06)` |

## Paper Grain

`.pdc-canvas-grain` adds a non-interactive SVG noise overlay at approximately 4% opacity. It must:

- cover the canvas without changing layout;
- preserve pointer interaction with the canvas;
- remain subtle at normal and zoomed views;
- respect rounded corners where present.

## Tailwind Integration

Extend Tailwind with named `gold`, `teal`, `surface`, and `pdc` colors while preserving existing semantic shadcn tokens. Semantic tokens in `index.css` map to the new dark palette.

## Compatibility

- Keep the standalone application file and `frontend/public/index.html` synchronized.
- Preserve existing functional styles and module-specific status colors unless they conflict with supplied canonical tokens.
- Remove or supersede old global theme overrides that redefine canonical gold, teal, surface, typography, or scrollbar values.
- Do not modify API behavior or application data.

## Verification

1. Build the React frontend successfully.
2. Validate inline-script syntax in the standalone HTML.
3. Confirm both HTML entry files remain identical.
4. Verify canonical token values and font imports through static checks.
5. Verify the production login and main application surfaces render in dark mode.
6. Verify a `.pdc-canvas` fixture uses the light paper palette, local ink colors, provenance washes, Caveat annotations, and grain overlay without affecting adjacent dark UI.
7. Deploy the frontend to Railway and confirm a successful deployment and healthy public response.
