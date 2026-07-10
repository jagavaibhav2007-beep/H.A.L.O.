# Halo UI/UX: Design Language

The visual and motion foundation every screen inherits. Style: **Liquid Glass, calm-premium** (user decision: restrained Apple-like motion, not cinematic). Source of truth for behavior: [systemdesign/](../systemdesign/00-overview.md); build stack: [techstack/10-ui](../techstack/10-ui.md).

## Color tokens (light / dark)
| Token | Light | Dark | Use |
|---|---|---|---|
| `--primary` | `#2563EB` | `#3B82F6` | royal blue — actions, focus, active nav |
| `--accent-soft` | `#93C5FD` | `#60A5FA` | baby blue — glows, gradients, voice states |
| `--bg` | `#F8FAFC` | `#0B1220` | window backdrop behind glass |
| `--surface` | `rgba(255,255,255,.65)` | `rgba(15,23,42,.55)` | glass panels (backdrop-blur 24px) |
| `--text` | `#0F172A` | `#E2E8F0` | primary text (≥4.5:1 on surface, both modes) |
| `--text-muted` | `#64748B` | `#94A3B8` | secondary text (≥3:1) |
| `--border` | `rgba(255,255,255,.5)` | `rgba(148,163,184,.15)` | 1px glass edges |
| `--tier-3` | `#D97706` | `#F59E0B` | approval-needed states |
| `--destructive` | `#DC2626` | `#EF4444` | delete, money, irreversible |
| `--success` | `#059669` | `#10B981` | done, passed |

Semantic tokens only — no raw hex in components. Dark mode is designed, not inverted; contrast checked per mode.

## Typography
- **Inter** (300/400/500/600) everywhere; **JetBrains Mono** for code/diffs/logs.
- Scale: 12 (labels) · 13 (feed) · 14 (body) · 16 (chat) · 20 (panel titles) · 28 (empty states). Line-height 1.5–1.6. Tabular figures for costs/timers.

## Glass rules
- Surfaces: blur 24px, soft 1px light border, one elevation scale (companion < panel < card < modal). No stacked blurs (GPU + legibility).
- **Reduced-transparency fallback:** if the OS asks (or GPU is weak), glass degrades to solid `--bg`-tinted surfaces. Nothing may be readable only through blur.

## Motion (calm premium)
- Tokens: `fast 150ms` (hover/press) · `base 200ms` (state changes) · `slow 300ms` (panel transitions) · orb expand→workspace 250ms. **Nothing over 400ms.**
- Ease-out on enter, ease-in on exit; exits ~70% of enter duration. Motion only where it carries meaning — one animated element per view.
- Modals/cards scale+fade **from their trigger** (spatial continuity). Lists stagger 30ms/item, capped at 8.
- `prefers-reduced-motion`: all non-essential motion off; states change by color/opacity only.

## Interaction constants
- Click targets ≥ 32px (desktop) with visible hover (`fast` tint) + press (scale .97) states; cursor-pointer on everything clickable.
- Focus: 2px `--primary` outline, 2px offset — visible on glass, never removed. Full keyboard path: global hotkey summons, `Esc` collapses, `Ctrl+K` command input, tab order = visual order.
- Icons: **Lucide** only, 1.5px stroke, 16/20/24 sizes. Never emoji.
- Every async state shows feedback ≤300ms (skeleton or inline spinner); every list has a designed empty state; destructive buttons are red, physically separated, and confirm.

## Voice of the interface (copy rules)
- Halo speaks first person, one sentence, plain: "I need your OK to send this email." Never jargon ("Tier-3 interrupt raised").
- Errors state cause + a way forward: "Deepgram is unreachable — I'll show replies as text for now."
