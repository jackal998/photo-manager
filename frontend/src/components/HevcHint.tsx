// HevcHint — the "install HEVC Video Extensions" notice (#869).
//
// WHEN it shows is decided in lib/hevcHint.ts (once per page life, the first
// time a video surface chooses the transcode on a Windows engine whose #787
// probe said "unsupported"). This component only draws it.
//
// It is the Q9 caution banner, not a new visual: the frame is the shared
// CAUTION_BANNER class string the Execute dialog's two banners wear, with the
// same ▲ glyph leading the text. Two details follow the ruling rather than
// invent one:
//   * «Caution never becomes a button. If a caution banner needs an action,
//     the action is a normal button below it.» — so "Dismiss" and "Don't show
//     again" are the ordinary outline Button primitive, BELOW the banner.
//   * The Store link sits inside the sentence as an underlined inline anchor,
//     the same treatment AllDeleteBanner gives its jump anchors, and the
//     sentence is ONE translated template split on its `{link}` placeholder so
//     a locale can put the product name wherever its grammar needs it.
//
// Rendered once, in the preview pane under its title strip: the pane is always
// mounted and hosts two of the three video surfaces (single preview + the
// grid's tiles). A transcode chosen inside the full-res viewer raises the same
// notice in the pane, where it is waiting when the viewer closes.
//
// `role="status"`, not `alert`: nothing failed — the video still plays, via
// the transcode — so it must not interrupt a screen-reader user mid-sentence.

import { useT } from "@/i18n/useT";
import { Button } from "@/components/ui/button";
import {
  HEVC_STORE_URL,
  dismissHevcHint,
  dismissHevcHintForever,
  useHevcHintStore,
} from "@/lib/hevcHint";
import {
  PREVIEW_HEVC_HINT,
  PREVIEW_HEVC_HINT_DISMISS,
  PREVIEW_HEVC_HINT_NEVER,
  PREVIEW_HEVC_HINT_STORE_LINK,
} from "@/testids";
import { CAUTION_BANNER } from "./execute/cautionBanner";

const TEMPLATE_EN =
  "This browser cannot decode HEVC; videos are being converted first. " +
  "Install {link} from the Microsoft Store for native playback.";

export function HevcHint() {
  const t = useT();
  const visible = useHevcHintStore((s) => s.visible);
  if (!visible) return null;

  // `{link}` is deliberately NOT passed as a param: interpolate() leaves an
  // unmatched placeholder verbatim, so it survives as the split marker. A
  // translation that drops it still renders the whole sentence — the link
  // simply lands at the end (AllDeleteBanner's recipe).
  const sentence = t("web.preview.hevc_hint", TEMPLATE_EN);
  const marker = sentence.indexOf("{link}");
  const before = marker >= 0 ? sentence.slice(0, marker) : sentence;
  const after = marker >= 0 ? sentence.slice(marker + "{link}".length) : "";

  return (
    <div data-testid={PREVIEW_HEVC_HINT} className="flex-shrink-0 px-4 pt-3">
      <div role="status" className={CAUTION_BANNER}>
        <span aria-hidden="true" className="shrink-0 text-[12px] leading-[1.5]">
          ▲
        </span>
        <span>
          {before}
          <a
            data-testid={PREVIEW_HEVC_HINT_STORE_LINK}
            href={HEVC_STORE_URL}
            className="underline font-semibold hover:brightness-125 focus:outline-none focus:ring-1 focus:ring-caution-line rounded"
          >
            {t("web.preview.hevc_hint_link", "HEVC Video Extensions")}
          </a>
          {after}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid={PREVIEW_HEVC_HINT_DISMISS}
          onClick={dismissHevcHint}
        >
          {t("web.preview.hevc_hint_dismiss", "Dismiss")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid={PREVIEW_HEVC_HINT_NEVER}
          onClick={dismissHevcHintForever}
        >
          {t("web.preview.hevc_hint_never", "Don't show again")}
        </Button>
      </div>
    </div>
  );
}
