# Design QA — Breeze TTS 2 Desktop 0.2.2

## Comparison target

- Source visual truth:
  - `test_reports/design-qa-v0.2.2/00-reference-index-batch.png`
  - `test_reports/design-qa-v0.2.2/00-reference-index-timeline.png`
- Packaged implementation:
  - `test_reports/design-qa-v0.2.2/10b-packaged-batch-top-2487x1268.png`
  - `test_reports/design-qa-v0.2.2/15-packaged-batch-calibrated.png`
  - `test_reports/design-qa-v0.2.2/19-packaged-timeline-calibrated.png`
- Same-input comparison evidence:
  - `test_reports/design-qa-v0.2.2/17-compare-calibrated-full.png`
  - `test_reports/design-qa-v0.2.2/18-compare-calibrated-focus.png`
  - `test_reports/design-qa-v0.2.2/21-compare-timeline-focus.png`

## Viewport and normalization

- Source pixels: 2487 × 1268 and 2263 × 1280.
- Packaged implementation pixels: 1265 × 712 for the stable desktop capture; 1668 × 1015 for the calibrated wide and timeline captures.
- CSS viewport: desktop passes used 1280 × 900 and a wide 2000 × 1022 override at device scale factor 1. The in-app browser's visible bitmap was narrower than the requested wide viewport, so the wide comparison uses the actual 1668 × 1015 bitmap and does not claim pixel-perfect frame sizing.
- Density normalization: the Windows source captures were rendered at 125% display scaling. For the calibrated comparison they were cropped to the corresponding visible region and bicubic-downsampled to 1668 × 1015. The normalized source and packaged screenshot were then placed side by side in one bitmap. This comparison is reliable for palette, hierarchy, typography treatment, component shape, relative spacing and interaction affordances; exact outer-canvas margins are intentionally not scored because the source CSS viewport metadata is unavailable.
- State: Eager runtime loaded, `多角色 / 批量 / SRT` selected, four realistic Chinese/English lines restored, editable track and sentence table visible. The second source screenshot and the implementation timeline capture use equivalent timeline/table states but different vertical scroll positions; the focused comparison aligns the corresponding regions rather than pretending they are the same full-page crop.

## Full-view comparison evidence

`17-compare-calibrated-full.png` places the source and the packaged 0.2.2 page in the same image. The implementation preserves the requested IndexTTS 2.5 visual language: cool off-white canvas, pink-to-blue hero gradient, deep navy display text, pink active accents, pale-blue action band, white controls, restrained shadows and horizontal functional tabs. The product-specific information architecture is intentionally different: Breeze exposes six honest functional pages and natural-language direction instead of copying IndexTTS acceleration pages or eight-dimensional emotion controls.

## Focused region comparison evidence

- `18-compare-calibrated-focus.png` compares the hero, action band and navigation. Typography weight, gradient direction, button treatment, active-tab underline and spacing rhythm follow the source.
- `21-compare-timeline-focus.png` compares the editable sentence region. The implementation retains the editable table and adds a bidirectionally synchronized draggable track, per-line direction controls, keyboard-accessible edge handles, project import/export and SRT write-back.
- Separate focused evidence was required because table labels and timeline controls are too small to judge reliably in the full-view comparison.

## Required fidelity surfaces

- Fonts and typography: both use the Windows system UI stack with a heavy navy display heading and compact UI labels. The Breeze heading is slightly larger at narrower widths, an intentional responsive choice; wrapping, truncation and hierarchy remain stable.
- Spacing and layout rhythm: hero, action band, tabs and work cards follow the reference sequence. Launcher configuration is isolated on the home screen, so normal work pages are not occupied by startup settings. Desktop, 465 px mobile and long-table states were captured; controls do not overlay the generation target or collapse into a tall mobile sidebar.
- Colors and visual tokens: pink/blue gradient, navy foreground, pink active/primary states, pale-blue surfaces and white controls match the source family. The primary pink button now uses a dark foreground for readable contrast.
- Image quality and asset fidelity: the source is UI-only and contains no logo, illustration or product photography that must be reproduced. No placeholder imagery, emoji, CSS illustration or custom inline SVG substitutes were introduced. Native controls and text labels remain sharp at both capture densities.
- Copy and content: all fixed copy is Breeze-specific and standalone. It explicitly says that per-line control uses natural-language performance directions and does not falsely claim IndexTTS eight-dimensional emotion support.
- Icons and affordances: the product relies on labelled buttons and native control indicators rather than decorative icon approximations. Import, parse, move, single-line, delete, save, export and SRT actions remain explicit.
- Accessibility and responsiveness: semantic tabs, labelled textboxes/comboboxes, per-line accessible names, disabled/loading states, visible focus styling, readable contrast, 42 px controls and 24 px timeline edge handles were verified. At 465 × 872 the action tools and tabs become horizontal scrollers and timeline rows become card-form controls.

## Primary interactions tested

- Open the packaged launcher without preloading the model.
- Start Eager mode and observe staged loading plus elapsed time.
- Enter the six-page workspace and switch tabs.
- Parse four Chinese/English lines and restore the automatic draft.
- Render synchronized timeline blocks and editable sentence rows.
- Verify per-line voice, language, direction, time and action labels.
- Generate a real three-line GPU batch, retain three checkpoints and merge a 24 kHz WAV.
- Use runtime unload, close the packaged window, and confirm the backend port and package process tree are released.
- Scan the final desktop log for EPIPE, uncaught JavaScript errors, startup failure and pipe errors: none in the 0.2.2 run.

## Findings

- P0: none.
- P1: none remaining.
- P2: none remaining.
- P3: very wide timeline tables intentionally keep horizontal scrolling instead of shrinking editable fields below usable sizes.

## Comparison history

### Iteration 1 — source feature parity

- [P1] The initial Breeze page lacked the source's visible SRT/TXT/JSON import, default role/language controls and editable batch workflow.
- [P2] The source's `导入 → 解析 → 编辑生成` orientation and timeline/table relationship were absent.
- Fixes: added import/parse controls, role and language defaults, four supported script paths plus project JSON, role analysis, editable track/table synchronization, SRT write-back, project import/export and natural-language per-line direction.
- Post-fix evidence: `test_reports/design-qa-v0.2.2/06-dialogue-desktop.png` and `test_reports/design-qa-v0.2.2/07b-real-batch-merged.png`.

### Iteration 2 — interaction and responsive polish

- [P2] The generation action dock could overlay result content.
- [P2] Narrow screens stacked the entire toolbar vertically and consumed too much of the usable page.
- [P2] Empty voice/history/queue states and diagnostics did not always provide a clear next action.
- Fixes: made the action dock part of normal flow, converted mobile tools/tabs to horizontal scrollers, added empty-state CTAs, history filters/pagination, actionable diagnostics, staged launch progress and explicit accessible names.
- Evidence before/after: `test_reports/design-qa-v0.2.2/03-generate.png`, `test_reports/design-qa-v0.2.2/03b-generate-no-overlay.png`, `test_reports/design-qa-v0.2.2/04-settings-mobile.png`, and `test_reports/design-qa-v0.2.2/05-dialogue-mobile.png`.

### Iteration 3 — packaged release pass

- Fixes verified in the real `app.asar`: desktop 0.2.2 launched, loaded the model, restored and rendered four timeline rows, unloaded the runtime and exited without EPIPE or orphaned child processes.
- Post-fix comparison evidence: `17-compare-calibrated-full.png`, `18-compare-calibrated-focus.png` and `21-compare-timeline-focus.png`.
- Result: no actionable P0/P1/P2 visual, responsive, accessibility or interaction findings remain.

## Follow-up polish

- Optional P3: if a future release adds a shared icon library, the import and project actions can receive source-consistent line icons without changing the current labelled affordances.

## Final result

passed
