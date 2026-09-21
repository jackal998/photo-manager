import '@testing-library/jest-dom'
// msw request handlers are wired in 2B when there are real API endpoints to mock.

// Radix primitives (DropdownMenu, Select, …) call pointer-capture and
// scrollIntoView APIs that jsdom does not implement. Polyfill them as no-ops
// (only when absent) so component tests can open menus without throwing.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {}
}
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

// jsdom implements no ResizeObserver, and two components now measure their own
// box with one: ResultTree (the sticky header's height feeds the virtualiser's
// scrollMargin, and the table's width drives column shedding) and Toolbar (its
// width drives the manifest-open shed, lib/toolbarShed.ts). A no-op here —
// only when absent, same rule as the three polyfills above — keeps any test
// that renders them from throwing on mount.
//
// It never FIRES, so every measurement stays null and each component takes its
// documented "not measured yet" path: render everything, shed nothing. Tests
// that need a real measurement install their own observer and restore it
// afterwards (ResultTree.scrollMargin.test.tsx's stubResizeObserver), which
// still works — it overwrites this one rather than racing it.
if (!('ResizeObserver' in globalThis)) {
  ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
