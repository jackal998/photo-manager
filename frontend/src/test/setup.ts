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
