// The caution banner's one class string (layout slice E · open questions Q9).
//
// Until this slice the execute dialog's two warning strips were on Tailwind's
// stock `amber-*` scale — the last off-palette colours left in the themed tree,
// a cold-yellow rectangle on warm paper. Q9 specced the role instead:
//
//   background   #fdf3d9   (--color-caution-bg)
//   border       1px solid #e3c264 with a 3px LEFT rule
//   radius       8px · padding 10px 12px
//   text         #6b4e12 (--color-caution-ink), 13px / 1.5, weight 500
//   glyph        ▲ 12px, leading the text
//
// One exported string rather than a component, because the two banners differ
// in everything except their frame: one carries clickable jump anchors built
// from a split template, the other a single interpolated sentence. A shared
// wrapper component would have to take children plus a glyph flag plus a role,
// which is more API than the 10 characters it would save.
//
// The caution role is deliberately pulled toward yellow-olive and away from
// the terracotta accent so a warning can never be misread as the primary
// action, and the ▲ is what carries it in grayscale (danger ⊗ dark fill /
// light label; caution ▲ and positive ✓ are both pale, so their glyphs are
// the separation).
export const CAUTION_BANNER =
  "flex items-start gap-2 rounded-[8px] border border-caution-line " +
  "border-l-[3px] bg-caution-bg px-[12px] py-[10px] text-[13px] " +
  "font-medium leading-[1.5] text-caution-ink";
