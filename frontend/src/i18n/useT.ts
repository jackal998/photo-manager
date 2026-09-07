// Translation hook: useT() returns a bound t() function that reads the
// current catalog from the i18n store.
//
// t(key, fallback, params?)
//   - Looks up key in catalog; uses fallback if key is absent or catalog empty.
//   - Substitutes {name} placeholders with params[name].
//   - The English fallback is the build-time default — the UI degrades
//     gracefully before the catalog loads (same pattern as react-intl
//     defaultMessage).

import { useI18nStore } from "./useI18nStore";

/**
 * Substitute {name} placeholders in a template string.
 * Any placeholder with no matching param is left as-is.
 */
export function interpolate(
  template: string,
  params?: Record<string, string | number>
): string {
  if (params === undefined) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) => {
    const value = params[key];
    return value !== undefined ? String(value) : match;
  });
}

/**
 * Translate a key using the given catalog.
 * Pure function — exported for unit testing without the store.
 */
export function translate(
  catalog: Record<string, string>,
  key: string,
  fallback: string,
  params?: Record<string, string | number>
): string {
  const template = catalog[key] ?? fallback;
  return interpolate(template, params);
}

/**
 * React hook. Returns a t() function bound to the current catalog.
 * Subscribes to the i18n store so the component re-renders on locale change.
 */
export function useT(): (
  key: string,
  fallback: string,
  params?: Record<string, string | number>
) => string {
  const catalog = useI18nStore((s) => s.catalog);
  return (key, fallback, params) => translate(catalog, key, fallback, params);
}
