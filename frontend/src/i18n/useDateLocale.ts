// The BCP-47 tag every date cell formats with, derived from the APP's locale.
//
// One hook rather than three call sites reading the store: `formatDate`'s
// `locale` argument is optional, so a call site that forgets it silently falls
// back to the BROWSER's locale — which is not a crash, not a test failure, and
// not visible to anyone developing on an en-* machine. It shipped exactly that
// way: 「2024年2月1日」 in the Shot Date column of an English UI.

import { useI18nStore } from "./useI18nStore";
import { dateLocaleFor } from "../lib/format";

/** React hook. Returns the date locale for the current UI language. */
export function useDateLocale(): string {
  const locale = useI18nStore((s) => s.locale);
  return dateLocaleFor(locale);
}
