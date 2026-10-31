# photo-manager — Feature Inventory

Canonical catalogue of user-visible behaviour. Each section names one
feature: where it is reachable, what the user does to trigger it, what
happens, and which conditions change the result.

This file is the answer to "is this behaviour documented?" and
"what's the expected UX for X?". It complements — does not replace —
the happy-path walkthrough in [`README.md` § Usage](../README.md#usage).

The full backfill ships in two stages: [PR #263](https://github.com/jackal998/photo-manager/pull/263)
seeded the Execute Action cluster; a later PR completed the inventory
(scan, review, save/load, preview, i18n). `docs_guard` now requires an
update here whenever user-visible behaviour changes — see
[#262](https://github.com/jackal998/photo-manager/issues/262).

> **Reading this after the #646 cutover.** Every entry describes the shipped
> web client (FastAPI backend + React frontend);
> [#888](https://github.com/jackal998/photo-manager/issues/888) rewrote the
> entries that still described the removed desktop client. Headings without a
> `Web —` prefix predate the port and keep their names so in-page links stay
> stable. An entry whose behaviour did not survive the port says **removed
> with the desktop client in #646** in its Entry point. Any other mention of
> the desktop client is history — where a behaviour, a decision or a
> divergence came from — never a description of current behaviour.

Rule IDs such as `R-LOCK-1` refer to [docs/behaviour/rules.md](behaviour/rules.md); the register is the source of truth for those rules.

---

## Index

Each entry lives in its Area file under [`features/`](features/). The table below is generated from those files by `python scripts/gen_features_index.py`.

<!-- features-index:start -->
| Feature | Area |
|---|---|
| [Bulk regex — remove from list (deferred decision)](features/bulk-operations.md#bulk-regex--remove-from-list-deferred-decision) | Bulk operations |
| [Context menu — Lock / Unlock](features/context-menu.md#context-menu--lock--unlock) | Context menu |
| [Context menu — Open Folder](features/context-menu.md#context-menu--open-folder) | Context menu |
| [Context menu — Set Action (per-file)](features/context-menu.md#context-menu--set-action-per-file) | Context menu |
| [Developer tool — memory probe](features/scan.md#developer-tool--memory-probe) | Scan |
| [Empty area context menu](features/context-menu.md#empty-area-context-menu) | Context menu |
| [Empty-state action buttons](features/main-window.md#empty-state-action-buttons) | Main window |
| [Execute Action — base flow](features/execute-action.md#execute-action--base-flow) | Execute Action |
| [Execute Action — complete-group delete confirm](features/execute-action.md#execute-action--complete-group-delete-confirm) | Execute Action |
| [Execute Action — complete-group warning banner with jump-to](features/execute-action.md#execute-action--complete-group-warning-banner-with-jump-to) | Execute Action |
| [Execute Action — dialog geometry persistence](features/execute-action.md#execute-action--dialog-geometry-persistence) | Execute Action |
| [Execute Action — filter by action type (#502)](features/execute-action.md#execute-action--filter-by-action-type-502) | Execute Action |
| [Execute Action — lock-confirm dialog](features/execute-action.md#execute-action--lock-confirm-dialog) | Execute Action |
| [Execute Action — partial execution via "Execute selected"](features/execute-action.md#execute-action--partial-execution-via-execute-selected) | Execute Action |
| [Execute Action — preview pane](features/execute-action.md#execute-action--preview-pane) | Execute Action |
| [Execute Action — scope to selected groups (only-selected entry, #410, #430, #429)](features/execute-action.md#execute-action--scope-to-selected-groups-only-selected-entry-410-430-429) | Execute Action |
| [Exit dirty-flag prompt](features/main-window.md#exit-dirty-flag-prompt) | Main window |
| [Keep-worthiness scoring](features/review.md#keep-worthiness-scoring) | Review |
| [Language switch](features/i18n.md#language-switch) | i18n |
| [List menu — Remove from List](features/menus.md#list-menu--remove-from-list) | Menus |
| [Log menu](features/menus.md#log-menu) | Menus |
| [Main window — close-during-scan confirm](features/main-window.md#main-window--close-during-scan-confirm) | Main window |
| [Main window — column order/width persistence](features/main-window.md#main-window--column-orderwidth-persistence) | Main window |
| [Main window — geometry + splitter persistence](features/main-window.md#main-window--geometry--splitter-persistence) | Main window |
| [Main window — keyboard navigation](features/main-window.md#main-window--keyboard-navigation) | Main window |
| [Main window — results tree double-click](features/main-window.md#main-window--results-tree-double-click) | Main window |
| [Main window — row selection (no horizontal auto-scroll)](features/main-window.md#main-window--row-selection-no-horizontal-auto-scroll) | Main window |
| [Main window — sort persistence within session](features/main-window.md#main-window--sort-persistence-within-session) | Main window |
| [Main window — status bar baseline](features/main-window.md#main-window--status-bar-baseline) | Main window |
| [Open Manifest — base flow](features/file-operations.md#open-manifest--base-flow) | File operations |
| [Preview pane — byte-budget LRU cache](features/preview-pane.md#preview-pane--byte-budget-lru-cache) | Preview pane |
| [Preview pane — full-resolution viewer](features/preview-pane.md#preview-pane--full-resolution-viewer) | Preview pane |
| [Preview pane — no-autoplay video default](features/preview-pane.md#preview-pane--no-autoplay-video-default) | Preview pane |
| [Preview pane — per-device request coordinator + 1-ahead prefetch](features/preview-pane.md#preview-pane--per-device-request-coordinator--1-ahead-prefetch) | Preview pane |
| [Review — Undo of Keep best under named confirmation](features/review.md#review--undo-of-keep-best-under-named-confirmation) | Review |
| [Save Manifest Decisions — base flow](features/file-operations.md#save-manifest-decisions--base-flow) | File operations |
| [Scan dialog — auto-select after scan](features/scan.md#scan-dialog--auto-select-after-scan) | Scan |
| [Scan dialog — auto-select aggressive ("delete all others")](features/scan.md#scan-dialog--auto-select-aggressive-delete-all-others) | Scan |
| [Scan dialog — collapse Advanced Settings](features/scan.md#scan-dialog--collapse-advanced-settings) | Scan |
| [Scan dialog — exiftool workers (setting-only)](features/scan.md#scan-dialog--exiftool-workers-setting-only) | Scan |
| [Scan dialog — folder list (no priority arrows)](features/scan.md#scan-dialog--folder-list-no-priority-arrows) | Scan |
| [Scan dialog — hash pool mode](features/scan.md#scan-dialog--hash-pool-mode) | Scan |
| [Scan dialog — hash workers (NAS-aware auto, no UI control)](features/scan.md#scan-dialog--hash-workers-nas-aware-auto-no-ui-control) | Scan |
| [Scan dialog — multi-source scan](features/scan.md#scan-dialog--multi-source-scan) | Scan |
| [Scan dialog — read-knee autotune opt-out](features/scan.md#scan-dialog--read-knee-autotune-opt-out) | Scan |
| [Scan dialog — stage / throughput / ETA progress (#424)](features/scan.md#scan-dialog--stage--throughput--eta-progress-424) | Scan |
| [Scan flow — manifest summary in progress log](features/scan.md#scan-flow--manifest-summary-in-progress-log) | Scan |
| [Scan flow — rescan confirm](features/scan.md#scan-flow--rescan-confirm) | Scan |
| [Scan flow — visual selection of KEEP rows after scan](features/scan.md#scan-flow--visual-selection-of-keep-rows-after-scan) | Scan |
| [Scan grouping — complementary RAW + lossy pair](features/scan.md#scan-grouping--complementary-raw--lossy-pair) | Scan |
| [Scan walk — recycle-bin / system folders are never walked](features/scan.md#scan-walk--recycle-bin--system-folders-are-never-walked) | Scan |
| [Scan walk — skip-on-error traversal](features/scan.md#scan-walk--skip-on-error-traversal) | Scan |
| [Set Action dialog — dual-section Simple + Regex view](features/set-action-dialog.md#set-action-dialog--dual-section-simple--regex-view) | Set Action dialog |
| [Set Action dialog — geometry persistence](features/set-action-dialog.md#set-action-dialog--geometry-persistence) | Set Action dialog |
| [Set Action dialog — live preview + validation](features/set-action-dialog.md#set-action-dialog--live-preview--validation) | Set Action dialog |
| [Set Action dialog — numeric comparison panel](features/set-action-dialog.md#set-action-dialog--numeric-comparison-panel) | Set Action dialog |
| [Set Action dialog — Score / Lock / Resolution fields](features/set-action-dialog.md#set-action-dialog--score--lock--resolution-fields) | Set Action dialog |
| [Similarity column](features/review.md#similarity-column) | Review |
| [Singleton-prune offer after destructive ops (#426)](features/execute-action.md#singleton-prune-offer-after-destructive-ops-426) | Execute Action |
| [Web preview pane — group grid view (multi-tile)](features/web-ui.md#web-preview-pane--group-grid-view-multi-tile) | Web UI |
| [Web preview pane — group sync controller (GroupMediaController)](features/web-ui.md#web-preview-pane--group-sync-controller-groupmediacontroller) | Web UI |
| [Web preview pane — title, image frame, metadata table, decision block](features/web-ui.md#web-preview-pane--title-image-frame-metadata-table-decision-block) | Web UI |
| [Web preview pane — video playback (V1 streaming)](features/web-ui.md#web-preview-pane--video-playback-v1-streaming) | Web UI |
| [Web preview pane — video transcode fallback (V2 H.264)](features/web-ui.md#web-preview-pane--video-transcode-fallback-v2-h264) | Web UI |
| [Web — Action dialog (Set Action by Field)](features/web-ui.md#web--action-dialog-set-action-by-field) | Web UI |
| [Web — app shell never scrolls; the tree does (#894)](features/web-ui.md#web--app-shell-never-scrolls-the-tree-does-894) | Web UI |
| [Web — Context menu: Apply best-copy decisions to this group](features/web-ui.md#web--context-menu-apply-best-copy-decisions-to-this-group) | Web UI |
| [Web — Context menu: stays inside the window (#897)](features/web-ui.md#web--context-menu-stays-inside-the-window-897) | Web UI |
| [Web — Daylight theme and results-tree framing](features/web-ui.md#web--daylight-theme-and-results-tree-framing) | Web UI |
| [Web — desktop shell (pywebview launcher)](features/web-ui.md#web--desktop-shell-pywebview-launcher) | Web UI |
| [Web — Execute (only selected)](features/web-ui.md#web--execute-only-selected) | Web UI |
| [Web — filesystem browse dialog](features/web-ui.md#web--filesystem-browse-dialog) | Web UI |
| [Web — main result tree multi-selection](features/web-ui.md#web--main-result-tree-multi-selection) | Web UI |
| [Web — manifest decision persistence](features/web-ui.md#web--manifest-decision-persistence) | Web UI |
| [Web — overlay window geometry (movable / resizable / persisted)](features/web-ui.md#web--overlay-window-geometry-movable--resizable--persisted) | Web UI |
| [Web — preview panel resizable + persistence](features/web-ui.md#web--preview-panel-resizable--persistence) | Web UI |
| [Web — result-tree column sort and resize persistence](features/web-ui.md#web--result-tree-column-sort-and-resize-persistence) | Web UI |
| [Web — scan dialog invalid source path](features/web-ui.md#web--scan-dialog-invalid-source-path) | Web UI |
| [Web — scan dialog: subfolders by default, empty result stays open](features/web-ui.md#web--scan-dialog-subfolders-by-default-empty-result-stays-open) | Web UI |
| [Web — scan progress throughput / ETA / localized stages (#740)](features/web-ui.md#web--scan-progress-throughput--eta--localized-stages-740) | Web UI |
| [Web — scan progress, skips and errors in the app log](features/web-ui.md#web--scan-progress-skips-and-errors-in-the-app-log) | Web UI |
| [Web — scan SSE connection-drop watchdog (#661)](features/web-ui.md#web--scan-sse-connection-drop-watchdog-661) | Web UI |
| [Web — ScanDialog source/output persistence](features/web-ui.md#web--scandialog-sourceoutput-persistence) | Web UI |
| [Web — settings dialog](features/web-ui.md#web--settings-dialog) | Web UI |
| [Web — sort by decision (Action header)](features/web-ui.md#web--sort-by-decision-action-header) | Web UI |
<!-- features-index:end -->

---

## How to update this file

When user-visible behaviour changes (button label, conditional dialog,
action scope, new keyboard shortcut, new menu item, post-action state
change, new condition gating a flow), add or update the corresponding
entry in its Area file under [`features/`](features/) in the same PR.

Cross-cutting rules (lock gate, Skip vs Delete, Execute scope and similar) are
stated once in [`behaviour/rules.md`](behaviour/rules.md). Cite a rule by its ID
there instead of restating it; `scripts/check_rules_register.py` checks the IDs.

History (why a behaviour changed, "Last verified" dates, "Previously" notes) goes to git, news fragments and CHANGELOG, not into entries; the archive of pre-2026-10-14 history is [`docs/audits/features-history-2026-10-14.md`](audits/features-history-2026-10-14.md).

The [`update-docs` skill](../.claude/skills/update-docs/SKILL.md)'s
"User-visible behaviour changed?" row is the trigger. The
[`docs_guard` hook](../scripts/hooks/docs_guard.py) enforces the touch
at `gh pr create` time, and again in the `pr-gates` CI job. A
non-trivial change (10 or more changed lines, or a changed declaration)
to an existing file under `app/web/routes/` or `frontend/src/components/`
(test files excluded) blocks until an entry file under `features/` is updated.
A new file under `frontend/src/components/` or `frontend/src/hooks/` is pointed
at the features files too, but any doc touch satisfies that half of the gate.

If a change genuinely doesn't shift user-visible behaviour, the bypass
token `[docs-not-needed: <reason>]` in the `gh pr create` body still
works — but the reason must be specific and reviewer-visible. Since
#857 that is enforced, not advised: a token whose reason is blank
bypasses nothing and blocks with "bypass token seen but its reason is
empty — write why", both in the local hook and in the `pr-gates` CI
job. #858 adds the placeholder itself — a reason that is exactly
`<reason>`, pasted from a template or quoted in prose — to what is
rejected, so documenting the convention never disables a gate. The
same rules apply to `[qa-not-needed: <reason>]` and to the changelog
gate's `[skip-news: <reason>]`; the canonical statement of all three
lives in [`news/README.md`](../news/README.md) § Bypass.

### Section schema

Each feature is one section using this template:

```markdown
### <Feature name>

- **Area:** <one value from AREA_VOCABULARY in scripts/gen_features_index.py>
- **Entry point:** <UI location> — `<file:line>`
- **Trigger:** <user action that activates it>
- **Behaviour:** <1-3 sentences on expected UX>
- **Conditions / variants:** <state that changes the behaviour>
- **Related:** PR #<N>, issue #<N>, qa scenario `sNN_<name>.py`
```

The index table at the top is generated, not hand-edited: it lists every entry heading (case-insensitively sorted by feature name) with the entry's `Area` line. After adding or changing an entry, run `python scripts/gen_features_index.py`; `python scripts/gen_features_index.py --check` (CI job `features-index`) fails when the committed index is stale. The `Area` value must be one of the names in `AREA_VOCABULARY` in that script.
