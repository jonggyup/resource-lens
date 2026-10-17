# Terminal UI review and adoption decisions

Reviewed 2026-10-16. The objective was to make existing Resource Lens features
findable and predictable while retaining its resource topology and verified
control workflow. This was a documentation/visual review, not a performance
benchmark or a claim that every upstream application was installed and exercised.

## Sources and observations

| Project | Primary material inspected | Useful pattern |
| --- | --- | --- |
| htop | [Upstream manual](https://github.com/htop-dev/htop/blob/main/htop.1.in) | Function-key action bar, F1 help, F4 filter, F10 exit, pointing-device support |
| btop | [README and screenshots](https://github.com/aristocratos/btop#screenshots), [normal UI screenshot](https://github.com/aristocratos/btop/blob/main/Img/normal.png) | Distinct resource panels and directly clickable highlighted keyboard actions; dense charts alongside numeric readings |
| k9s | [Upstream key bindings](https://github.com/derailed/k9s#key-bindings) | Colon resource navigation, filtering, help, keyboard list navigation and escape from modes |
| lazygit | [Upstream bindings](https://github.com/jesseduffield/lazygit/blob/master/docs/keybindings/Keybindings_en.md), [current-panel help](https://lazygit.dev/keybindings/) | Help and actions scoped to the active panel; predictable selection/confirmation flow |

The btop normal screenshot was visually inspected, including its panel borders,
resource grouping, chart/numeric balance and labeled controls. Its image was
used only for local review and is not bundled with this project. The other
observations above are based on the linked first-party manuals and bindings.

## Implemented in 0.6.0

1. A clickable, context-sensitive bottom action bar. Buttons are only drawn if
   their entire label fits. F1 provides the full context help at small widths.
   This combines htop's visible function keys and btop's clickable key hints.
2. F1/? help starts with the active view's controls, including a distinct CPU
   placement context. Dismissing help restores scroll position. While help is
   open, resource actions and tab mouse events are blocked. This fixes an actual
   issue in the previous global-help implementation rather than adding decoration.
3. A colon-triggered searchable view switcher, inspired by k9s resource navigation.
   Aliases include memory/ram, network/net, services/workloads, and view numbers.
   Results support keyboard or mouse selection. No arbitrary commands or shell
   execution are accepted.
4. j/k navigation outside text-entry fields, plus F4 filter and F10 quit aliases.
   Existing shortcuts and control confirmations remain available.
5. Persistent mode text on the status line, including detail pages. Resource
   controls remain separate from navigation and retain their existing previews.

## Retained or deferred

The existing six-card overview, resource colors, topology tiles and expanded
panels already serve the same grouping purpose seen in btop; no wholesale
redesign was justified. A resizable pane framework, theme engine and plugin
command system would add complexity before addressing a demonstrated workflow.
We also did not copy htop's destructive process-key assignments: existing Resource
Lens keys have different contexts, and resource changes need explicit previews.

All implementation in this change is original. No upstream implementation,
theme or image assets were copied into the distributed package. Attribution here
records design inspiration rather than an added code dependency.

## Verification

114 automated tests pass. Added checks cover alias navigation, text-entry
isolation, help dismissal/scroll restoration, blocked controls while in help,
clickable footer quit propagation, filter completion, action-bar bounds and
preservation of active-change exit acknowledgement. Live terminal checks and
captured screens are recorded in [navigation-evidence.json](navigation-evidence.json).
