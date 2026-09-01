# Visual auto-select feasibility — `moment_id` and `visual_score`

**Date:** 2026-09-02 · **Status:** DRAFT — Phase 0 (dimension census) complete; Phases 1 (labelling) and 2 (measurement) not started.
**Branch:** `docs/visual-autoselect-feasibility`, based on `origin/docs/web-port-feasibility` tip `e9e7471`. Every `file:line` below was read on THIS tree.

## The question

Can a lightweight, strictly on-device desktop app (a) cluster photos into "same moment" bursts, and (b) pick the best photo of a moment — without a cloud service, without a GPU requirement, and without breaking the determinism contract the existing `group_id` depends on? This document fixes what Phase 0 established so that Phase 1 and Phase 2 have a stable base to measure against. It answers *what could be built and at what dependency cost*. It does **not** yet answer *whether it should be* — that verdict is Section 6 and it is empty on purpose.

## The two axes (decided)

| Axis | Column | Status |
|---|---|---|
| `group_id` | existing | **Untouched.** Its semantics, its union-find, and its lex-min canonical id do not change. |
| `moment_id` | **new** | A second, independent union-find over a separate edge set. Same algorithm, separate column. |
| `visual_score` | **new** | A per-file pixel-derived score. Separate from the existing metadata `score`. |

`moment_id` and `visual_score` are separate features with separate evidence bars. Neither is a modification of an existing column.

## Evidence rules (this document's contract)

1. **Every measured claim carries a 4-tuple**: probe path + SHA + args + JSON output. A number without all four is not a measurement.
2. **`(lit.)` marks a literature figure** and carries a URL and the hardware the source names. A `(lit.)` figure is never promoted to a fact about this rig.
3. **`not verified` is a valid cell** and is used in preference to a plausible guess. Section 8 consolidates every one of them.
4. **Where two Phase 0 reports disagree, both readings appear with attribution.** Nothing is silently reconciled.
5. **Structural cost is not wall-clock cost.** The classical census's cost column counts passes over the working array. It orders signals; it says nothing about milliseconds.
6. **No unlabelled `~N`.** An approximate figure is allowed only when it carries its provenance in the same cell — a URL, a `(lit.)` tag, a named probe, or `TBD Phase 2`. A round number standing on its own is the exact failure mode rule N1 of the scanner-perf retro exists to catch. A `[secondary]` source stays labelled `[secondary]`.

This is the same bar `docs/audits/scanner-perf-retro-2026-06-08.md` set after the #604→#610 saga (its rules N1 and N4), applied to a feasibility study instead of a perf claim.

---

# 1. Dimension census

**Status legend.** `KEEP-FREE` = rides the existing exiftool call, no new dependency. `KEEP-T0` = numpy/scipy/PIL, already installed. `KEEP-T1` = needs `opencv-python-headless` (43.8 MB, gated install). `KEEP-T2` = needs `onnxruntime` + small ONNX models (~33 MB, gated). `KEEP-T3` = needs an embedding model (+~84 MB, gated). `DROP` = adjudicated out, with the reason in the cell. `—` = **not adjudicated in Phase 0**; the row is carried so Phase 1 can decide, and its absence from the keep list is not a rejection.

Installed today (verified in `.venv`, `phase0-repo-facts.md` §6): `numpy 2.4.4`, `scipy 1.17.1`, `pillow 12.2.0`, `pillow_heif 1.3.0`, `rawpy 0.26.1`, `ImageHash 4.3.2`. Absent per the same pass: `cv2`, `onnxruntime`, `torch`, `scikit-image`, `mediapipe`, `pyiqa`, `insightface`. `exiftool 13.11` on `PATH`. **`pywt 1.8.0` (transitive) comes from a different source** — `phase0-selection-classical.md:31`; the repo-facts pip grep never covered PyWavelets, so that one package is attributed to the classical census, not to §6.

## 1a. Grouping axis — candidate signals for `moment_id`

Source: `phase0-grouping-census.md`. Tag names verified against the exiftool Perl tag tables (`exiftool.org/TagNames/*.html` returns HTTP 404 from this environment — see Section 8).

| # | Signal | What it measures | Needs | Pipeline slot (branch `file:line`) | Prior art | Status |
|---|---|---|---|---|---|---|
| 1 | `EXIF:DateTimeOriginal` (0x9003) | Shutter time, 1-second resolution, camera-local, no zone | nothing — already extracted | selector `scanner/exif.py:777`; parsed `scanner/exif.py:834-844`; persisted as `shot_date` `scanner/manifest.py:25` | digiKam *Group Selected by Time*, "plus or minus two seconds" — https://docs.digikam.org/en/main_window/image_view.html | KEEP-FREE (the base signal) |
| 2 | `EXIF:SubSecTimeOriginal` (0x9291) + `Composite:SubSecDateTimeOriginal` | Fractional seconds; the composite merges DTO + SubSec + Offset into one sortable string | one extra `-Tag` selector | add to arg list `scanner/exif.py:774-794`; new `MediaExtract` field `scanner/media_extract.py:48`; new column `infrastructure/manifest_repository.py:84-105` | immich puts `SubSecDateTimeOriginal` **first** in its date priority list — https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts | **KEEP-FREE** |
| 3 | `EXIF:OffsetTimeOriginal` (0x9011) | UTC offset for `DateTimeOriginal` | one extra selector | same slot as #2 | same immich composite — https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts | **KEEP-FREE** |
| 4 | `QuickTime:CreateDate` / `ContentCreateDate` | Video shot time, frequently UTC where the still is camera-local | nothing — already extracted | `scanner/exif.py:777`, `scanner/exif.py:882` | immich's list includes `CreationDate`, `MediaCreateDate` — https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts | — (the UTC/local mismatch is why `docs/grouping-topology.md` §2 rejected a date gate for the same-stem edge) |
| 5 | `EXIF:GPSDateStamp` + `GPSTimeStamp` (composite `GPSDateTime`) | A UTC clock independent of the camera's own | one extra selector | `scanner/exif.py:774-794` | immich lists `GPSDateTime` as a date fallback — https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts | — (clock-correction use only, not an edge) |
| 6 | `MakerNotes:BurstUUID` (Apple 0x000b) | **Explicit** burst membership. exiftool note: *"unique ID for all images in a burst"* | one extra selector | `scanner/exif.py:774-794` → `media_extract.py` field → new column | immich → `autoStackId` https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts ; Apple `PHAsset.burstIdentifier` https://developer.apple.com/documentation/photos/phasset/burstidentifier ; tag source https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Apple.pm | **KEEP-FREE — the single strongest free signal where it exists** (ground truth, not an inference). Branch caveat: §7 |
| 7 | `MakerNotes:ContentIdentifier` (Apple 0x0011) / `MediaGroupUUID`; `QuickTime:ContentIdentifier` | Live Photo still↔video pairing; groups the derivatives of one capture | two extra selectors | `scanner/exif.py:774-794`; the edge belongs beside the same-stem edge `scanner/dedup.py:733-805` | immich `livePhotoCID` → `linkLivePhotos` https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts ; PhotoPrism https://github.com/photoprism/photoprism/issues/3960 ; QuickTime `Keys` table https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/QuickTime.pm | **KEEP-FREE** — strictly improves the existing filename-only pair edge |
| 8 | `XMP-GCamera:BurstID` + `BurstPrimary` | Explicit burst membership on Google Camera / Pixel; `BurstPrimary` is the camera's own best-frame pick | one extra selector | `scanner/exif.py:774-794` | immich `getAutoStackId` tries `BurstID` first https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts ; tag source https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Google.pm | **KEEP-FREE**. `BurstPrimary` is also a free `visual_score` prior |
| 9 | `XMP-GCreations:CameraBurstID` | Burst id written by Google Photos "creations" | nothing | same slot | immich fallback https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Google.pm | **KEEP-FREE** |
| 10 | `EXIF:ImageUniqueID` (0xa131); Apple `MakerNotes:ImageUniqueID` (0x0015) | Per-capture id — equal ids = same capture, different rendition | nothing | same slot | PhotoPrism stacks on it https://github.com/photoprism/photoprism-docs/blob/master/docs/user-guide/organize/stacks.md | — (groups renditions, **not** burst siblings) |
| 11 | Sony `SequenceImageNumber`, `SequenceFileNumber`, `SequenceLength`, `ShotNumberSincePowerUp` | Position within a burst and the burst's declared length | extra selectors | same slot | exiftool forum burst-tag thread https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Sony.pm | **KEEP-FREE** (seq counters) |
| 12 | Sony `SequenceNumber` (0xb04a) + `ReleaseMode` (0xb049) / `ReleaseMode2` | *"shot number in continuous burst"*; release mode says continuous drive was engaged | nothing | same slot | https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Sony.pm | **KEEP-FREE**. `ReleaseMode` PrintConv `5 => 'Exposure Bracketing'` doubles as a bracket detector |
| 13 | Canon `ContinuousDrive` | Drive mode: `Single` / `Continuous` / `Continuous, High` / … | nothing | same slot | https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Canon.pm | **KEEP-FREE** — a **gate**, not an id |
| 14 | Canon `FileNumber` (0x8), `FileIndex` / `DirectoryIndex` / `ShutterCount` | Monotone per-body counters | nothing | same slot | PhotoPrism *Sequential Name* https://github.com/photoprism/photoprism-docs/blob/master/docs/user-guide/organize/stacks.md ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Canon.pm | **KEEP-FREE** (seq counters). exiftool note: `ShutterCount` *"may be valid only for some 1DmkIII copies"* |
| 15 | Nikon `ShutterCount` (0x00a7) + `FileNumber` / `DirectoryNumber` | *"Number of shots taken by camera so far"* | nothing | same slot | https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Nikon.pm | **KEEP-FREE** (seq counters) |
| 16 | FujiFilm `SequenceNumber` (0x1101), `DriveMode`/`DriveSpeed`, `ImageCount` (0x1438) | Frame index; drive mode + fps | nothing | same slot | https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/FujiFilm.pm | **KEEP-FREE** (seq counters). `ImageCount` *"may reset to 0 when new firmware is installed"* |
| 17 | Panasonic `BurstMode` (0x2a) + `SequenceNumber` (0x2b) | Burst / bracketing mode and frame index | nothing | same slot | https://www.dpreview.com/forums/thread/4205422 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Panasonic.pm | **KEEP-FREE** (seq counters); PrintConv covers AEB / Focus / WB / Aperture bracketing |
| 18 | Apple `ImageCaptureType` (0x0014), `LivePhotoVideoIndex` (0x0017), `RunTime` (0x0003) | Capture kind; Live Photo frame offset; monotonic device uptime | nothing | same slot | osxphotos https://github.com/RhetTbull/osxphotos ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Apple.pm | — (`RunTime` is a same-device monotone ordering signal independent of wall clock) |
| 19 | `XMP-GCamera:MotionPhoto`, `MotionPhotoVersion`, `MicroVideo*` | Marks a Google motion photo | nothing | same slot | Google Camera 7.5 naming change https://9to5google.com/2020/08/20/google-camera-7-5-pxl/ ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Google.pm | — (marks a single file; use to suppress a false one-file "moment") |
| 20 | Samsung `SamsungMotionPhotoVersion` (0x0a31), `MotionPhotoAutoPlayVideo` (0x0a33) | Samsung motion-photo marker | nothing | same slot | https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Samsung.pm | — . **A Samsung burst id is DROP: it does not exist** (only two commented-out undecoded blobs in `Samsung.pm`) |
| 21 | `EXIF:Make` + `EXIF:Model` | Body identity — the cheapest same-moment **gate** | nothing — both already requested | selectors `scanner/exif.py:784`; values counted and discarded at `scanner/exif.py:854` | PhotoPrism *Place & Time* https://github.com/photoprism/photoprism-docs/blob/master/docs/user-guide/organize/stacks.md | **KEEP-FREE** — the bytes are already read and thrown away |
| 22 | `EXIF:BodySerialNumber` (0xa4a1) / `CameraSerialNumber` (0xa4a0); maker serials | Two bodies of the *same model* at one event | extra selectors | `scanner/exif.py:774-794` | two-photographer collections are the documented failure mode, `docs/grouping-topology.md` §2 ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Canon.pm | — |
| 23 | `EXIF:LensModel` (0xa98d) / `LensMake` (0xa98c) / `LensSerialNumber` (0xa4a5) | Lens continuity; a lens change is a hard moment boundary on an ILC | `LensModel` already read | `LensModel` selector `scanner/exif.py:786`; others in the same list | https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Exif.pm | **KEEP-FREE** (Make/Model/Lens) |
| 24a | Settings continuity — the five that ARE already requested: `ISO`, `ExposureTime`, `FNumber`, `FocalLength`, `WhiteBalance` | Shots of one moment share exposure settings; a jump in focal length marks a boundary | nothing — all five already extracted | selectors `scanner/exif.py:784-787`; all five are in `_CENSUS_TAGS` `scanner/exif.py:463-476`; values counted then discarded `scanner/exif.py:854` | PhotoTOC clusters on time **and** colour https://www.microsoft.com/en-us/research/publication/phototoc-automatic-clustering-for-browsing-personal-photographs/ | **KEEP-FREE — cheapest unexploited signal in the census.** The bytes are already read and thrown away |
| 24b | Settings continuity — `ExposureMode` (0xa402), `ExposureProgram` (0x8822) | A switch out of a program mode marks a boundary | **one extra selector each**, like rows 2 and 3 | add to the arg list `scanner/exif.py:774-794` | same PhotoTOC https://www.microsoft.com/en-us/research/publication/phototoc-automatic-clustering-for-browsing-personal-photographs/ ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Exif.pm | **KEEP-FREE (one extra selector).** **Correction:** these two are **not** extracted today — neither string appears anywhere in the branch tree and neither is in `_CENSUS_TAGS` (`scanner/exif.py:463-476`). The source report `phase0-grouping-census.md:52` carried the same overclaim, calling all seven "already extracted"; verified false here |
| 25 | Bracket detector: Canon `BracketMode`/`BracketValue`/`BracketShotNumber`; Sony `BracketShotNumber`; Fuji `AutoBracketing`; Panasonic `BurstMode`∈{AEB,…}; `EXIF:ExposureCompensation` (0x9204) | Separates an AEB/HDR bracket from a burst of one subject | extra selectors | same slot | digiKam names exposure-bracketed grouping as a use case https://docs.digikam.org/en/main_window/image_view.html ; https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/Canon.pm | — . **Matters for `visual_score`: inside a bracket, "correct exposure" is not the goal** |
| 26 | GPS proximity: `GPSLatitude`/`GPSLongitude`/`GPSAltitude`, `GPSHPositioningError` | Spatial co-location | presence already computed; **values** need retaining | `gps_present` `scanner/exif.py:846` from selectors `scanner/exif.py:779`; retaining numbers = `media_extract.py:83` field change + columns | PhotoPrism *Place & Time* requires position **and** same second https://github.com/photoprism/photoprism-docs/blob/master/docs/user-guide/organize/stacks.md ; accuracy https://www.gps.gov/gps-accuracy | **DROP as a feature.** *"typically accurate to within a 4.9 m radius under open sky"* **(lit., https://www.gps.gov/gps-accuracy, 2026-09-02)** and worse near buildings, so it cannot be a positive burst signal; absent on most ILC bodies. Usable only as a negative gate (500 m apart ⇒ not one moment) |
| 27 | Filename sequence continuity: `IMG_1234`→`IMG_1235`, `DSC_`, `DJI_`, `PXL_<YYYYMMDD_HHMMSSsss>`, `IMG_E1234` | Adjacent frame numbers on the same body | nothing — filenames already walked | `cluster_map` `scanner/walker.py:296-324`; `pair_cluster` field `scanner/walker.py:67` | PhotoPrism *Sequential Name* https://github.com/photoprism/photoprism-docs/blob/master/docs/user-guide/organize/stacks.md ; digiKam *Group by Filename* https://docs.digikam.org/en/main_window/image_view.html ; Pixel ms-in-filename https://9to5google.com/2020/08/20/google-camera-7-5-pxl/ | — . Counters wrap at 9999 and reset on card format; the unconditional filename pair edge across merged folders was the actual cause of the "—" passenger (`docs/grouping-topology.md` §2, fixed by #539 at `scanner/dedup.py:733-805`) |
| 28 | Existing same-stem `pair_cluster` edge | RAW+JPG / HEIC+MOV renditions of one capture | nothing — shipped | `scanner/walker.py:296-324` → `scanner/dedup.py:733-805` → `scanner/dedup.py:806-840` | digiKam *Group by Filename* for JPG+RAW https://docs.digikam.org/en/main_window/image_view.html | — . A moment edge should **reuse** this, not duplicate it |
| 29 | pHash / dHash at a **loose** threshold, candidates from the existing BK-tree | Visual near-continuity across a burst | nothing — `phash`/`dhash` already computed and persisted | computed `scanner/hasher.py:200-201`; distance `scanner/phash_distance.py:18`; BK-tree `scanner/dedup.py:543-600` (`query` `:580`); persisted `scanner/manifest.py:18` + `idx_phash` `scanner/manifest.py:36` | `docs/grouping-topology.md` §3 survey of CC-vs-leader clustering; Czkawka's published Hamming ladder https://raw.githubusercontent.com/qarmin/czkawka/master/czkawka_core/src/tools/similar_images/mod.rs | **KEEP-T0 (loose pHash via BK-tree)**. The tree's docstring (`scanner/dedup.py:552-558`) says it is *"candidate generation only"* and `query(key, threshold)` takes the threshold as an argument — a second query at a looser threshold reuses the index with no second file read. Dedup default threshold 10 (`scanner/dedup.py:734`) |
| 30 | `mean_color` continuity | Global colour/lighting continuity | nothing — already computed via a 1×1 LANCZOS downscale | `scanner/hasher.py:196-197`; persisted `scanner/media_extract.py:64` | PhotoTOC https://www.microsoft.com/en-us/research/publication/phototoc-automatic-clustering-for-browsing-personal-photographs/ ; Cooper et al. ACM TOMM 2005 https://dl.acm.org/doi/10.1145/1083314.1083317 | — . Free, 3 bytes/photo, very coarse |
| 31 | Colour histogram / colour-layout continuity | Scene continuity at more than 3 numbers per image | PIL only (hard dep); extra CPU inside the existing decode | `scanner/hasher.py:187-206`, after `draft("RGB",(256,256))` at `scanner/hasher.py:187` and `convert("RGB")` at `:188` | PhotoTOC https://www.microsoft.com/en-us/research/publication/phototoc-automatic-clustering-for-browsing-personal-photographs/ ; Cooper et al. https://dl.acm.org/doi/10.1145/1083314.1083317 | **KEEP-T0 (colour hist)**. Rides the existing single read + decode. Requires a `HASH_RECIPE_VERSION` bump (`scanner/hasher.py:108`, currently `"3"`) |
| 32 | Global embedding cosine similarity (CLIP / DINOv2 / MobileCLIP / SigLIP class) | Semantic scene identity | a model **and** a runtime; neither installed | same slot as #31, reusing the decoded image; embedding persisted as a new column | Apple MobileCLIP CVPR 2024 https://arxiv.org/pdf/2311.17049 , https://github.com/apple/ml-mobileclip | **KEEP-T3 (embedding cosine, quantised + pinned)**. MobileCLIP-S0 checkpoint = **215,934,653 bytes** (HF file-metadata API, https://huggingface.co/apple/MobileCLIP-S0 , 2026-09-02); *(lit.)* **11.4 M params, 1.5 ms image encoder** per https://github.com/apple/ml-mobileclip/blob/main/README.md — that README states the latency is measured on iPhone 12 Pro Max (Apple Neural Engine), **not a Windows CPU and not measured here** |
| 33 | `mtime` / `ctime` | Last resort when every date tag is absent | nothing — already collected | `scanner/media_extract.py:75-76`; columns `scanner/manifest.py:26-27` | Shotwell groups into *Events* by date https://shotwell-project.org/doc/html/ | — . Copying/moving rewrites these; documented fallback only |
| 34 | Directory locality | Files in one folder are usually one import/session | nothing | `scanner/walker.py:296-324` | PhotoPrism https://github.com/photoprism/photoprism/issues/4309 | — . A weak **gate**; merged/Takeout folders break it (`docs/grouping-topology.md` §2) |

**Dropped because the tag does not exist** (verified, not assumed): no Samsung burst identifier is exposed by exiftool (`Samsung.pm` has two commented-out undecoded blobs, `# 0x09e0-name - seen 'Burst_Shot_Info'`); `grep -c Burst` returns **0** in both https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/DJI.pm and https://raw.githubusercontent.com/exiftool/exiftool/master/lib/Image/ExifTool/GoPro.pm . Samsung / DJI / GoPro bursts must fall back to time + settings + pixels.

**Sub-second coverage is maker-dependent and is a bonus, never a precondition.** iPhone (iOS 13+) writes all three sub-second tags; Pixel puts milliseconds in the *filename*; Sony is model-specific (a7 III / a7R III round to whole seconds, a9 II writes milliseconds); Canon / Nikon / Fujifilm are reported to write it on modern bodies with **no vendor documentation found**; Samsung and DJI are not established either way. Sources: https://www.dpreview.com/forums/threads/sub-second-datetimeoriginal-in-exif.4307268/ and https://9to5google.com/2020/08/20/google-camera-7-5-pxl/ . Consequence: any moment rule must be correct from 1-second `DateTimeOriginal` alone and merely *tighten* when sub-second exists. The maker sequence counters (#11–#17) are the reliable intra-second ordering signal, not SubSec.

**Determinism constraints carried forward** (from the grouping census §4, which derives them from `docs/grouping-topology.md` §3): fixed thresholds only, never data-dependent ones (a Cooper-style or Google-patent "gap larger than the average gap" rule re-partitions existing moments when one photo is added — https://patents.google.com/patent/US9411831B2/en); connected components, never a leader/greedy/medoid; floats may produce a boolean edge but may **never** choose an id; exact search, never an ANN index; every predicate symmetric by construction; a `moment_id` rule change needs its own version token, on the `HASH_RECIPE_VERSION` precedent.

## 1b. Selection axis — classical (non-learned) pixel signals

Source: `phase0-selection-classical.md`. **The cost column is structural** — passes over the W×H working array plus the heaviest kernel class — not milliseconds. **No literature ms/image figure was found for any signal in this table.** Dep classes: T0 = numpy/scipy/PIL (installed); T0w = + PyWavelets (installed transitively via `imagehash>=4.3`, `requirements.txt:15`, so an explicit line rather than a download); T1 = + `opencv-python-headless` **43.83 MB** wheel (PyPI JSON API, 2026-09-02, https://pypi.org/project/opencv-python-headless/#files ); T1c = + `opencv-contrib-python-headless` **53.65 MB**; T1s = + `scikit-image` **11.91 MB**.

| # | Family | Signal | Dep | Cost class | Key failure mode | Prior art | Status |
|---|---|---|---|---|---|---|---|
| 1 | sharpness | Variance of Laplacian (LAPV) | T0 | 1 conv + 1 reduce | Scales ~quadratically with resolution; bokeh reads as blur; noise inflates it | Pech-Pacheco et al. ICPR 2000 https://doi.org/10.1109/ICPR.2000.903548 ; https://pyimagesearch.com/2015/09/07/blur-detection-with-opencv/ | **DROP as a global metric — scale-dependent.** Survives only as the per-tile kernel of #16 |
| 2 | sharpness | Tenengrad (TENG) | T0 | 2 conv + 1 reduce | Same resolution dependence; high-contrast texture beats subject sharpness | Pertuz et al. *Pattern Recognition* 46(5) 2013 https://doi.org/10.1016/j.patcog.2012.11.011 | — |
| 3 | sharpness | Tenengrad variance (TENV) | T0 | 2 conv + 1 reduce | as #2 | https://doi.org/10.1016/j.patcog.2012.11.011 | — |
| 4 | sharpness | Brenner gradient | T0 | 1 shifted diff + 1 reduce (**cheapest in family**) | Horizontal-only; noise-sensitive; fixed 2-px step ties it to one resolution | Brenner et al. 1976 https://doi.org/10.1177/24.1.1254907 | — |
| 5 | sharpness | Sum-Modified-Laplacian (SML/LAPM) | T0 | 2 conv + 1 reduce | Absolute-value sum leaks brightness; normalise by mean | Nayar & Nakagawa TPAMI 1994 https://doi.org/10.1109/34.308479 | — |
| 6 | sharpness | Energy of Laplacian (LAPE) | T0 | 1 conv + 1 reduce | No mean subtraction, so exposure leaks in | Pertuz catalogue https://doi.org/10.1016/j.patcog.2012.11.011 | — |
| 7 | sharpness | Normalised gray-level variance (GLVN) | T0 | 1 reduce (**cheapest overall**) | Not a focus measure in flat scenes; useless across scenes | https://doi.org/10.1016/j.patcog.2012.11.011 | — |
| 8 | sharpness | Wavelet high-band energy (WAVS/WAVV) | T0w | 1 separable transform + 1 reduce | Haar is noise-sensitive; sub-band energy scales with resolution | https://pywavelets.readthedocs.io/en/latest/ref/2d-dwt-and-idwt.html | — |
| 9 | sharpness | FFT high-frequency energy **ratio** | T0 | 1 FFT + 2 reduces | `r0` set relative to working resolution; JPEG blocking adds spurious HF | https://pyimagesearch.com/2020/06/15/opencv-fast-fourier-transform-fft-for-blur-detection-in-images-and-video-streams/ | — . The ratio is scale-normalised, unlike #1-#8 |
| 10 | sharpness | **Crete–Roffet re-blur metric** | T0 (`scipy.ndimage.uniform_filter`) | 2 conv + 2 shifted diffs + 1 reduce | Fails on very low-texture frames (nothing to lose) | Crete, Dolmiere, Ladret & Nicolas, SPIE HVEI XII 2007 https://doi.org/10.1117/12.702790 | **KEEP-T0** — scale-normalised by construction, bounded ~0-1, survives mixed resolutions |
| 11 | sharpness | Marziliano edge width | T0 | 1 conv + a per-edge scan | **The Python-level edge walk holds the GIL** | Marziliano et al. ICIP 2002 https://infoscience.epfl.ch/bitstreams/1e2ac1b6-2647-438b-a587-4281c2e52ff5/download | — . Vectorise or drop (Section B) |
| 12 | sharpness | CPBD | T0 | 1 conv + per-block loop | Tuned on LIVE synthetic Gaussian blur; block loop holds the GIL | Narvekar & Karam IEEE TIP 2011 https://doi.org/10.1109/TIP.2011.2131660 | — |
| 13 | sharpness | JNB | T0 | as #12 | as #12; CPBD supersedes it | Ferzli & Karam IEEE TIP 2009 https://doi.org/10.1109/TIP.2008.2011760 | — |
| 14 | sharpness | digiKam `blurDetector` | T1 | 1 blur + Canny + 2 reduces | `max(edges)` on a **binary** Canny output is 255 whenever any edge exists, so the ratio degenerates to edge density; `lowThreshold=0.4` on 8-bit input means "every gradient is an edge" | https://github.com/KDE/digikam/blob/master/core/libs/dimg/filters/imgqsort/imagequalityparser_blur.cpp | **DROP — broken as written** |
| 15 | sharpness | digiKam `blurDetector2` | T1 | 1 blur + 1 conv + 1 reduce | Takes **max**, not variance, of the Laplacian — one hot pixel or specular highlight saturates it | same file https://github.com/KDE/digikam/blob/master/core/libs/dimg/filters/imgqsort/imagequalityparser_blur.cpp | **DROP — worse than plain variance-of-Laplacian** |
| 16 | subject-local | **Top-k tile sharpness** | T0 | 1 conv + a strided reduce | Fails when the subject is smaller than a tile, or a bright specular tile wins | Excire's global/face/eye sharpness tiers https://excire.com/en/best-culling-software/ | **KEEP-T0 — the single most important fix for bokeh.** A portrait at f/1.4 has one sharp tile and 63 soft ones |
| 17 | subject-local | Centre-weighted sharpness | T0 | 1 conv + 1 weighted reduce | Punishes deliberate rule-of-thirds placement (contradicts #39) | camera metering convention; no canonical paper | — . Tie-break only |
| 18 | subject-local | Saliency-weighted sharpness | T0 (spectral residual) / T1 | 1 FFT + 1 IFFT + 1 conv + 1 reduce | Spectral residual fires on high-contrast clutter; light trails are maximally salient | Hou & Zhang CVPR 2007 https://doi.org/10.1109/CVPR.2007.383267 | — |
| 19 | subject-local | Face-region sharpness | T0 metric; detection is T1/T2 | 1 conv + masked reduce **+ detection** | Haar cascades miss profiles, tilted heads and dark skin tones at a materially higher rate | Viola & Jones 2001 https://doi.org/10.1109/CVPR.2001.990517 ; https://narrative.so/compare/aftershoot-vs-narrative | metric free once boxes exist — detection is 1c |
| 20 | motion | Cepstrum peak | T0 | 1 FFT + 1 IFFT + peak search | Real camera shake is curved and spatially varying, smearing the peak | Lokhande et al. ACM SAC 2006 https://doi.org/10.1145/1141277.1141459 | — |
| 21 | motion | Radon transform of the log-spectrum | T0 / T1s | 1 FFT + N_angles rotations (**most expensive here**) | A 1° sweep is 180 rotations of the spectrum | https://scikit-image.org/docs/stable/api/skimage.transform.html#skimage.transform.radon | — |
| 22 | motion | **Directional gradient anisotropy (structure tensor)** | T0 | 2 conv + 3 smoothed products + analytic 2×2 eigen-solve | Cannot separate motion blur from anisotropic *content* (a picket fence, a striped shirt) | Förstner 1986; Weickert IJCV 31 1999 https://doi.org/10.1023/A:1008009714131 | **KEEP-T0** — much cheaper than #20/#21 and usually sufficient to separate shake from sharp |
| 23 | exposure | Mean luminance | T0 | 1 reduce (trivial) | A high-key portrait and a blown-out mistake have the same mean | ubiquitous | — . Never alone |
| 24 | exposure | **Clipping % at 0 and 255, per channel** | T0 | 1 reduce (trivial) | Specular highlights legitimately clip in almost every good frame — needs an area threshold, not zero tolerance | digiKam uses exactly this with `underExposurePercent = 5.0` / `overExposurePercent = 5.0` https://github.com/KDE/digikam/blob/master/core/libs/dimg/filters/imgqsort/imagequalityparser_exposure.cpp | **KEEP-T0 — highest value per unit cost in the exposure family** |
| 25 | exposure | Histogram entropy | T0 | 1 histogram + 1 reduce | **Maximised by noise** — a noisy ISO-12800 frame out-scores a clean one | Pertuz catalogue (HISE) https://doi.org/10.1016/j.patcog.2012.11.011 | — |
| 26 | exposure | Dynamic range / percentile spread | T0 | 1 partial sort | Low-key and silhouette shots have small spread *by intent* | Zone-system practice; no citable formula | — |
| 27 | exposure | Zone distribution (11 Adams zones) | T0 | 1 histogram + 1 reduce | Purely descriptive; needs a reference — **which a burst supplies** | Adams Zone System; no canonical implementation | — |
| 28 | exposure | HDR-ness / tone-compression | T0 | 2 conv + 2 reduces | Confuses a flat-lit scene with a tonemapped one | **no classical metric paper found — a construction, treat as unverified** | — |
| 29 | colour | Gray-world cast deviation | T0 | 1 reduce (trivial) | Wrong by assumption on a forest, a sunset, a red-jersey shot; reads golden hour as a cast | Buchsbaum 1980 https://doi.org/10.1016/0016-0032(80)90058-7 | — |
| 30 | colour | Mean / std saturation | T0 / T1 | 1 elementwise + 1 reduce | Saturation is a *style*; punishes film-emulation grades | ubiquitous | — . Meaningful only within a burst |
| 31 | colour | Hasler–Süsstrunk colourfulness | T0 | 2 elementwise + 4 reduces | Correlates with *appeal*, not correctness; a garish edit wins | Hasler & Süsstrunk SPIE 2003 https://infoscience.epfl.ch/record/33994/files/HaslerS03.pdf ; reference impl. https://pyimagesearch.com/2017/06/05/computing-image-colorfulness-with-opencv-and-python/ | — |
| 32 | colour | Inter-frame white-balance consistency | T0 | reuses #29 | Fails when the burst spans a real lighting change (flash on one frame) | **no classical metric paper found — a construction, treat as unverified** | — . Burst-relative, needs `group_id` |
| 33 | noise | Immerkær fast noise variance | T0 | 1 conv + 1 reduce (**cheapest noise estimate**) | Fine detail (foliage, fabric, hair) reads as noise, so a *sharper* frame scores noisier | Immerkær CVIU 64(2) 1996 https://doi.org/10.1006/cviu.1996.0060 | — |
| 34 | noise | **MAD-wavelet noise sigma** | T0w / T1s | 1 DWT + 1 median | Same texture confusion as #33, but the median rejects sparse large edge coefficients | Donoho & Johnstone *Biometrika* 81(3) 1994 https://doi.org/10.1093/biomet/81.3.425 ; https://scikit-image.org/docs/stable/api/skimage.restoration.html | **KEEP-T0** — more robust than #33 |
| 35 | noise | digiKam k-means noise detector | T1 | **iterative, 30 clusters × 3 restarts over N pixels — most expensive in this census** | The source itself crops to the **top-left 256×256 corner** "to speed-up computation time" — the metric then describes a corner, often sky | https://github.com/KDE/digikam/blob/master/core/libs/dimg/filters/imgqsort/imagequalityparser_noise.cpp | **DROP — do not copy this design** |
| 36 | contrast | RMS contrast | T0 | 1 reduce (trivial) | Rewards busy scenes over clean ones | Peli *JOSA A* 7(10) 1990 https://doi.org/10.1364/JOSAA.7.002032 | — |
| 37 | contrast | Michelson contrast (on percentiles) | T0 | 1 partial sort | With true min/max it is decided by two pixels; saturates to ~1 with any specular highlight | https://doi.org/10.1364/JOSAA.7.002032 | — |
| 38 | contrast | Local contrast (tile std) | T0 | 1 conv + 1 reduce | Tile size *is* the metric | https://doi.org/10.1364/JOSAA.7.002032 | — |
| 39 | composition | Rule-of-thirds saliency alignment | T0 | reuses #18 | Centred symmetric compositions score badly by design; **within a burst composition barely changes** | Datta et al. ECCV 2006 https://doi.org/10.1007/11744078_23 | — . Low value for this use case |
| 40 | composition | Horizon tilt via Hough | T1 | 1 edge conv + Hough accumulator | Fails indoors; confuses a table edge for a horizon; **tilt is constant within a burst** | https://docs.opencv.org/4.x/dd/d1a/group__imgproc__feature.html | — . A library-wide flag, not a burst selector |
| 41 | composition | Subject centring | T0 | reuses #18 | Directly contradicts #39 — pick one or they cancel | see #39 | — |
| 42 | composition | Edge density | T0 / T1 | 1 conv + 1 reduce | Measures busyness; a cluttered blurry frame beats a clean sharp one | this is what digiKam's `blurDetector` actually computes (#14) | — |
| 43 | NR-IQA | **BRISQUE** | T1c or `pyiqa`→T2 | 2 scales × (local mean + std convs) + 4 shifted products + SVR predict | **Trained on LIVE's five synthetic distortions** — real camera output is out-of-distribution; rates a shallow-DoF portrait as distorted. Model files: `brisque_model_live.yml` **567,815 B** + `brisque_range_live.yml` **1,356 B**, separate downloads | Mittal, Moorthy & Bovik IEEE TIP 2012 https://doi.org/10.1109/TIP.2012.2214050 ; https://github.com/opencv/opencv_contrib/tree/master/modules/quality/samples | **DROP (OOD).** Parent's instruction: measure once in Phase 2, then expect DO-NOT-BUILD |
| 44 | NR-IQA | **NIQE** | not in `cv2.quality`; `pyiqa`→T2, or a numpy reimplementation (T0 + a pristine-MVG parameter file) | similar to #43 minus the SVR; **cost unknown** | A correctly exposed, sharp, but boring frame and a beautiful one score alike | Mittal, Soundararajan & Bovik IEEE SPL 2013 https://doi.org/10.1109/LSP.2012.2227726 | **DROP (OOD).** Same measure-once-then-expect-DO-NOT-BUILD instruction |
| 45 | NR-IQA | PIQE | no OpenCV binding found; numpy reimpl. is T0 | 1 MSCN pass + per-block loop over 16×16 blocks | Essentially a noise/blockiness detector; weakest at ranking aesthetically similar frames; block loop holds the GIL | Venkatanath et al. NCC 2015 https://doi.org/10.1109/NCC.2015.7084843 | — |
| 46 | edit-detect | pHash / dHash Hamming distance | T0 — **already present** | zero marginal cost | pHash is **deliberately** invariant to brightness/contrast/gamma, so it cannot tell an original from its edit | https://github.com/JohannesBuchner/imagehash ; `scanner/hasher.py:200-201` | — |
| 47 | edit-detect | Histogram distance (chi-square / EMD) | T0 | 1 histogram + O(bins) compare | Fails on a crop (histogram shifts with no grading) | https://docs.opencv.org/4.x/d6/dc7/group__imgproc__hist.html | — . The natural complement to #46 |
| 48 | edit-detect | SSIM at low resolution | T0 / T1s | 5 conv + 1 reduce on a tiny array | Needs the frames aligned; any crop tanks it | Wang, Bovik, Sheikh & Simoncelli IEEE TIP 2004 https://doi.org/10.1109/TIP.2003.819861 | — . High SSIM + high histogram distance = a re-grade |
| 49 | edit-detect | Crop detection via phase correlation | T0 | 2 FFT + 1 IFFT + peak search | Detects translation only; scale needs log-polar (Fourier-Mellin) | Reddy & Chatterji IEEE TIP 1996 https://doi.org/10.1109/83.506761 | — |
| 50 | edit-detect | Mean-colour distance | T0 — **already present** | zero marginal cost | Extremely coarse; a global exposure shift moves it, a local dodge/burn does not | `scanner/hasher.py:196-197` | — . Free first-pass filter |
| L§7 | motion (burst) | **Dense optical flow (Farneback / DIS)** — *this row comes from the LEARNED census §7, not the classical one; it is listed here because it needs no model* | **T1** | one flow field per adjacent frame pair | Only meaningful *within* a burst; cost is per-pair, not per-image | Google Top Shot feeds optical flow into its learned score https://research.google/blog/top-shot-on-pixel-3/ | **KEEP-T1 (burst dynamics)** — the only signal in the adjudication that requires `cv2` |

**GIL behaviour is a prediction, not a measurement.** numpy elementwise/reduction ops are *expected* to release the GIL for large arrays (https://numpy.org/doc/stable/reference/c-api/array.html#threading-support), with the caveat that a metric built from twenty small calls on a 1024×683 array spends most of its time in Python-level dispatch holding the GIL. Whether `scipy.ndimage`, `numpy.fft` and `PyWavelets` release it is **not verified** — no documentation statement was found for any of the three. `cv2` is expected to release the GIL **and** to self-parallelise on its own thread pool, which is a second concurrency source that would oversubscribe against the scan pool; `cv2.setNumThreads(1)` is likely required (https://docs.opencv.org/4.x/db/de0/group__core__utils.html). Pure-Python loops (#11, #12, #13, #45) hold the GIL for their whole duration — that one is structural, not a prediction. This project's own recorded lesson is that Python-thread timing tests here must assert a latency collapse, never throughput scaling.

## 1c. Selection axis — learned (model-based) signals

Source: `phase0-selection-learned.md`. **MB = decimal megabytes (bytes / 1e6); exact byte counts come from the Hugging Face tree API or PyPI. Every ms figure is `(lit.)` with the hardware the source names.** Nothing here was benchmarked, installed, or executed.

**The anchoring benchmark.** The OpenCV Zoo publishes comparable CPU milliseconds for its whole model set on one machine (Intel Core i7-12700K), read from `benchmark/color_table.svg` at https://github.com/opencv/opencv_zoo (fetched 2026-09-02): YuNet 0.69 ms @160×120 · SFace 5.09 ms @112×112 · FER/Progressive-Teacher 1.79 ms @112×112 · LPD_YuNet 5.68 ms @320×240 · NanoDet 41.02 ms @416×416 · YOLOX 78.77 ms @640×640 · PPOCRDet-CN 18.76 ms @640×480. **Caveat that must travel with these numbers:** YuNet's 0.69 ms is at 160×120, and its cost at photo-realistic input sizes (320×320+) is **unknown** — the zoo publishes no other resolution. SFace's 5.09 ms is *per face crop*, so a six-person group shot costs six passes.

| Group | Model | What it gives | Size (exact where given) | CPU ms (lit.) | Licence | Status |
|---|---|---|---|---|---|---|
| face det | **YuNet** `face_detection_yunet_2023mar.onnx` | Face boxes + 5 landmarks | **232,589 B = 0.23 MB** https://huggingface.co/api/models/opencv/opencv_zoo/tree/main/models?recursive=true | **0.69 ms @160×120, i7-12700K** https://github.com/opencv/opencv_zoo | **MIT** https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md | **KEEP-T2 (0.2 MB).** digiKam's default detector since 8.5.0 https://www.digikam.org/news/2024-11-16-8.5.0_release_announcement/ |
| face det | YuNet int8 | same | **100,416 B = 0.10 MB** | unknown | MIT | — |
| face det | SCRFD-500MF (`buffalo_s`) | Face boxes + 5 landmarks | **2,524,817 B = 2.52 MB** https://huggingface.co/api/models/immich-app/buffalo_s/tree/main?recursive=true | unknown | **Non-commercial research only** | **DROP (licence)** |
| face det | SCRFD-10GF (`buffalo_l/det_10g.onnx`) | Face boxes, higher recall | **16,923,827 B = 16.92 MB** https://huggingface.co/api/models/public-data/insightface/tree/main/models?recursive=true | unknown | Non-commercial research only | **DROP (licence)** |
| face det | SCRFD 0.5g (PhotoPrism's) | Face boxes | 2.41 MB reported for `det_500m.onnx` (search result, **not** a primary file listing) | unknown | Non-commercial research only | **DROP (licence)** |
| face det | ULFD slim / RFB | Face boxes | **1.04 MB** / 1.11 MB https://github.com/Linzaer/Ultra-Light-Fast-Generic-Face-Detector-1MB | 9.5 ms @320×240 int8 on Raspberry Pi 4B; 6.33 ms @320×240 on iPhone 6s Plus (lit., same README) — **no x86 figure** | **not stated in the README** | — |
| face det | BlazeFace short-range | Face boxes + 6 keypoints | **unknown** | unknown | Apache 2.0 https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf | — |
| face det | YOLO-face / YOLO11n | Face boxes | YOLO11n **5 MB / 2.6M params** https://docs.ultralytics.com/models/yolo11 | unknown | **AGPL-3.0 or paid Enterprise** https://github.com/ultralytics/ultralytics | **DROP (licence — AGPL is a hard blocker)** |
| per-face | Face count / size fraction | arithmetic on YuNet boxes | free once detection runs | n/a | n/a | KEEP-T2 (free rider) |
| per-face | Face-crop sharpness | classical VoL on the box | free, no model | n/a | n/a | KEEP-T2 (free rider on 1b #16/#19) |
| per-face | **Expression / FER** `facial_expression_recognition_mobilefacenet_2022july.onnx` | 7-class FER — gives "smiling", not a graded aesthetic | **4,791,892 B = 4.79 MB**; int8 **1,364,007 B = 1.36 MB** | **1.79 ms @112×112, i7-12700K** https://github.com/opencv/opencv_zoo | Apache 2.0 | **KEEP-T2 (4.8 MB)** |
| per-face | Eyes-open, geometric (EAR) | eye-aspect-ratio over 6 points/eye | needs a 68/106/478-point landmarker | n/a | n/a | **YuNet's 5 landmarks are insufficient** — one point per eye cannot express openness |
| per-face | **Eyes-open + smile (learned)** — MediaPipe Face Landmarker blendshapes | 478 landmarks + 52 blendshapes https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker | **bundle size unknown** | unknown | Apache 2.0 (code samples); model-card licence not restated | **KEEP-T2 (TBD)** — whether the 52 blendshapes include `eyeBlinkLeft/Right` is **not verified**, and this is the single most important open item in the learned census |
| per-face | Landmarks 106-pt `2d106det.onnx` | 106 landmarks | **5,030,888 B = 5.03 MB** | unknown | Non-commercial research only | **DROP (licence)** |
| per-face | Landmarks 68-pt 3D `1k3d68.onnx` | 68 3-D landmarks | **143,607,619 B = 143.61 MB** | unknown | Non-commercial research only | **DROP (licence + size)** |
| per-face | Head pose (yaw/pitch/roll) | `cv2.solvePnP` on 5 or 68 landmarks + a 3-D reference face | free once landmarks exist | n/a | Apache 2.0 | — . Detects "looking away" with no extra model |
| per-face | Gaze (L2CS-Net, ETH-XGaze) | gaze direction | unknown | unknown | unknown | — . Likely beyond the value/cost line |
| per-face | Age / gender `genderage.onnx` | demographic inference | **1,322,532 B = 1.32 MB** | unknown | Non-commercial research only | **DROP** — licence, plus a privacy-sensitive inference the brief does not need |
| per-face | dlib 68-point predictor | 68 landmarks | ~99 MB (widely cited, **not verified**) | unknown | dlib is Boost; the trained `shape_predictor_68_face_landmarks.dat` derives from iBUG 300-W = **research-only** | **DROP (licence trap)** |
| identity | **SFace** | 128-d face embedding, 0.9940 zoo accuracy | **38,696,353 B = 38.70 MB**; int8 **9,896,933 B = 9.90 MB** | **5.09 ms @112×112, i7-12700K** | **Apache 2.0** https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/README.md ; paper https://arxiv.org/abs/2205.12010 ; original https://github.com/zhongyy/SFace | — . The only commercially clean face-recognition model found. Also digiKam's default recogniser |
| identity | ArcFace R50 `w600k_r50` | 512-d | **174,383,860 B = 174.38 MB** | unknown | Non-commercial research only | **DROP (licence)** |
| identity | MobileFaceNet `w600k_mbf` | 512-d | **13,616,099 B = 13.62 MB** | unknown | Non-commercial research only | **DROP (licence)** |
| identity | FaceNet (PhotoPrism's) | 512-d | unknown | unknown | code MIT; weights derive from VGGFace2/CASIA-WebFace with their own terms | — |
| IQA | **NIMA (MobileNet)** | single scalar aesthetic score | **unknown** — no canonical release asset. Size **proxy** = zoo MobileNetV2 classifier **13,964,571 B = 13.96 MB** | unknown. Paper: MobileNet "twice as fast" as Inception-v2 at 80.36% vs 81.51% https://arxiv.org/pdf/1709.05424 | Apache 2.0 for the TF reimplementations; weights vary by repo | **KEEP-T2** |
| IQA | CLIP-IQA | aesthetic/quality via CLIP | dominated by the CLIP backbone | unknown | not stated on the ModelCard page | — . Only sensible if CLIP is already loaded |
| IQA | TOPIQ | quality | ~35M params, ~19 GFLOPs (lit.) https://arxiv.org/abs/2308.03060 | unknown | see above | — |
| IQA | MUSIQ | quality at native aspect ratio | multi-scale Transformer, 384 hidden / 14 layers / 6 heads, "comparable to ResNet-50" https://arxiv.org/pdf/2108.05997 | unknown | Apache 2.0 (Google Research) | — |
| IQA | MANIQA / HyperIQA / DBCNN / PaQ-2-PiQ / LIQE | quality | unknown | unknown | unknown | — . No advantage over NIMA at this footprint |
| IQA | Q-Align | quality via an LMM | **too big** — LMM scale | unknown | unknown | **DROP (size)** |
| IQA | LAION aesthetic predictor v2 | aesthetic head on frozen OpenCLIP ViT-L/14 | head negligible; **ViT-L/14 backbone dominates** | unknown | **not confirmed** | **DROP (backbone size)** |
| IQA | *toolbox* `pyiqa` / IQA-PyTorch | all of the above https://github.com/chaofengc/IQA-PyTorch/blob/main/docs/ModelCard.md | pulls **torch = 122.0 MB** Windows wheel https://pypi.org/project/torch/#files before any weights | — | — | **DROP as a dependency.** Pick one metric, export it to ONNX, never ship torch |
| embed | **CLIP ViT-B/32 visual** (immich's default) | 512-d | **351,613,724 B = 351.61 MB** fp32 ONNX https://huggingface.co/api/models/immich-app/ViT-B-32__openai/tree/main?recursive=true | unknown | OpenAI CLIP weights: MIT | **DROP (352 MB)** |
| embed | CLIP ViT-B/32 textual | 512-d | **254,193,396 B = 254.19 MB** | unknown | MIT | **DROP** — grouping is image-to-image; the text tower is never needed |
| embed | **DINOv2 ViT-S/14** | 384-d | **21M params → ~84 MB fp32** (*arithmetic from the param count, not a published file size*) | unknown | **Apache 2.0** https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md | **KEEP-T3.** Best licence/size trade; self-supervised features aim at exactly the instance-retrieval task grouping needs |
| embed | DINOv2 ViT-B/14 | 768-d | 86M params | unknown | Apache 2.0 | — . 4× the small variant |
| embed | MobileCLIP-S0 | image + text | checkpoint **215,934,653 B**; image encoder 11.4M params | **1.5 ms image encoder — iPhone/Neural-Engine, NOT x86 CPU** https://arxiv.org/html/2311.17049v2 | **`apple-amlr`** (Apple ML Research licence) https://huggingface.co/apple/MobileCLIP2-S0 | — . Licence must be read before commercial use |
| embed | SigLIP base `ViT-B-16-SigLIP__webli` | — | unknown | unknown | Apache 2.0 | — . Larger patch-16 compute than ViT-B/32 |
| embed | MobileNetV2 features (zoo) | 1280-d pre-logit | **13,964,571 B = 13.96 MB** | unknown | Apache 2.0 | — . Cheapest embedding that exists here |
| embed | MobileNetV1 (zoo) / PP-ResNet50 (zoo) | — / 2048-d | **16,890,136 B = 16.89 MB** / **102,567,035 B = 102.57 MB** | unknown | Apache 2.0 | — |
| subject | U2-Netp / U2-Net | salient-object mask | **4.7 MB** https://github.com/xuebinqin/U-2-Net / **176.3 MB** | "~30 ms per image on CPU" per a third-party card https://huggingface.co/Heliosoph/u2net-onnx — **hardware not stated, not primary** | Apache 2.0 | — |
| subject | EfficientSAM-Ti / MobileSAM | promptable segmentation | **48,312,857 B = 48.31 MB**, int8 **20,479,928 B = 20.48 MB** / MobileSAM 9.66M params, **ONNX size not verified** https://docs.ultralytics.com/models/mobile-sam | unknown | Apache 2.0 (zoo) / Apache claimed but **not verified** | — |
| subject | PP-HumanSeg / MediaPipe person det. / `scrfd_person_2.5g` | person mask / boxes | **6,163,938 B = 6.16 MB** / **11,990,159 B = 11.99 MB** / **3,710,223 B = 3.71 MB** | unknown | Apache 2.0 / Apache 2.0 / **Non-commercial research only** | — / — / **DROP (licence)** |
| subject | NanoDet / YOLOX | generic objects | **3,800,954 B = 3.80 MB** / **35,858,002 B = 35.86 MB** | **41.02 ms @416×416** / **78.77 ms @640×640** | Apache 2.0 | — |
| motion | MoveNet SinglePose Lightning | 17 keypoints → peak action | **9,413,268 B = 9.41 MB**; int8 **2,788,976 B = 2.79 MB** https://huggingface.co/api/models/Xenova/movenet-singlepose-lightning/tree/main?recursive=true | unknown | Apache 2.0 claimed, **not verified from a primary card** | — . Bursts only |
| motion | MediaPipe pose / RTMPose-m | body pose | **5,557,238 B = 5.56 MB** / size unknown | — / 90+ FPS on i7-11700, ONNXRuntime, 1 thread, batch 1 https://arxiv.org/pdf/2303.07399 | Apache 2.0 / Apache claimed, **not verified** | — |

**Runtime wheels, Windows x86-64** (PyPI, fetched 2026-09-02; **download** sizes — installed on-disk size was not verified for any of them): `onnxruntime 1.29.0` **14.0 MB** MIT https://pypi.org/project/onnxruntime/#files · `onnxruntime-directml 1.24.4` **25.6 MB** https://pypi.org/project/onnxruntime-directml/#files · `opencv-python-headless 5.0.0.93` **43.8 MB** Apache 2.0 https://pypi.org/project/opencv-python-headless/#files · `mediapipe 1.0.1` **20.1 MB** Apache 2.0, **Python 3.9–3.12 only** https://pypi.org/project/mediapipe/#files · `torch 2.13.0` **122.0 MB** https://pypi.org/project/torch/#files · `openvino 2026.3.1` **75.8 MB** https://pypi.org/project/openvino/#files .

**Tier arithmetic.** T2 core = onnxruntime 14.0 + YuNet 0.23 + FER 4.79 + NIMA-proxy 13.96 = **32.98 MB**; + opencv-headless = 76.78 MB; + SFace int8 = 86.68 MB. T3 via DINOv2-S = T2-with-OpenCV + ~84 = **≈160.8 MB**; T3 via CLIP ViT-B/32 visual = 428.39 MB.

> **Placement settled 2026-09-02 by the parent session.** The NIMA-class aesthetic proxy (MobileNet-class, 13.96 MB size proxy) is **T2** and is already counted inside the 32.98 MB T2 core above; the adjudication table that showed it under T3 was a layout error. T3 is the DINOv2-S embedding only. Nothing else in the ladder depends on this.

**Why the learned census points at T2 and not T3 for selection.** The published best-frame work uses a customised MobileNet, not a transformer and not an IQA model: Google Top Shot started from vanilla MobileNet and combined it with optical flow, gyroscope data and 3A state (https://research.google/blog/top-shot-on-pixel-3/); the strongest published burst-selection paper is a **0.47 MB** model at **13 ms per frame on an iPhone 7 (lit.)** with **top-1 agreement with users 64.1%, top-3 86.2%** over bursts averaging ~11 frames (https://arxiv.org/pdf/1803.07212). Both score *relative* quality within a burst rather than absolute quality per image — an easier problem, and one the classical metrics already address where subject and lighting are constant. BuIQA argues explicitly that single-image IQA applied to burst frames fails because inter-frame differences are subtle and the scores are not discriminative (https://arxiv.org/pdf/2511.07958). Apple's key-frame patents describe a weighted sum of image-quality, shutter-lag and processing-latency components (https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10594952 , https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11838676).

**Licence red flags, verbatim.** InsightFace's README states *"The training data containing the annotation (and the models trained with these data) are available for non-commercial research purposes only"* and, dated 2025-11-24, *"For open-sourced face recognition models (e.g., buffalo_l package), please contact recognition-oss-pack@insightface.ai for licensing"* (https://github.com/deepinsight/insightface). **The MIT licence on the code does not extend to the weights.** That covers every `buffalo_*` and `antelopev2` pack, every SCRFD detector in them, `2d106det`, `1k3d68` and `genderage`. Ultralytics YOLO is AGPL-3.0 or paid Enterprise. MobileCLIP is `apple-amlr`, not an OSI licence. **Clean set:** YuNet (MIT), SFace (Apache 2.0), OpenCV Zoo models generally (Apache 2.0), DINOv2 (Apache 2.0), U2-Net (Apache 2.0), OpenAI CLIP weights (MIT), MediaPipe (Apache 2.0), onnxruntime (MIT), OpenVINO (Apache 2.0), opencv-python-headless (Apache 2.0).

**Privacy surface, if identity ever ships.** Face embeddings at rest are biometric data under GDPR Art. 9 and Illinois BIPA regardless of whether they leave the machine — a local-only app still creates a biometric database on the user's disk. The learned census recommends opt-in, a visible delete path, and storage in a file removable independently of the manifest, and flags this as a product decision to surface rather than default on. Its cheaper substitute: **within a burst**, faces seconds apart can be matched by bounding-box IoU and position, needing no recognition model at all.

## 1d. Working resolution — fixed at 1024 px long edge

**The measurement that forces this.** `scanner/hasher.py:187` calls `pil_img.draft("RGB", (256, 256))` before `convert("RGB")` at `:188`. Probed on synthetic test patterns with the project venv's Pillow 12.2.0 / libjpeg-turbo (`scratchpad/draft_ladder.py`, classical census §A):

| Source (JPEG) | Decoded at | Linear scale |
|---|---|---|
| 640×480 | 640×480 | 1/1 |
| 1920×1080 (2 MP) | 480×270 | 1/4 |
| 3008×2000 (6 MP) | 752×500 | 1/4 |
| 4032×3024 (12 MP, iPhone) | 504×378 | 1/8 |
| 6000×4000 (24 MP, APS-C) | 750×500 | 1/8 |
| 8256×5504 (45 MP, full-frame) | 1032×688 | 1/8 |
| PNG / WebP, any size | unchanged, full resolution | 1/1 |

Three findings, each of which independently breaks pixel metrics on the draft buffer:

1. **The ladder is non-monotonic in megapixels.** A 6 MP frame decodes to 752×500 while a **12 MP frame decodes to *smaller*, 504×378**. libjpeg offers only 1/1, 1/2, 1/4, 1/8, and Pillow picks the largest reduction still covering the request in both axes, so the binding constraint is `short_side / 8 >= 256`, i.e. short side ≥ 2048. A 2000 px-tall frame misses that by 48 pixels and gets twice the linear resolution of a 3024 px-tall one.
2. **`draft()` is JPEG-only.** PNG and WebP are untouched, and **HEIC is untouched too** — `pillow_heif.HeifImageFile` defines no `draft` of its own (`'draft' in HeifImageFile.__dict__` → `False`), so a 12 MP iPhone **HEIC decodes at 4032×3024 while the same shot as JPEG decodes at 504×378**, a 64× difference in pixel count. This matters because the library is 56.16 % JPEG and 26.20 % HEIC (§1e).
3. **RAW is a third regime.** The RAW branch (`scanner/hasher.py:147-168`) returns the embedded JPEG preview via `_load_raw_preview_from_bytes` (`scanner/hasher.py:483`, `extract_thumb` at `:503`) with no draft; the code's own comment at `scanner/hasher.py:155` cites "1024×768 for a 12 MP DNG", but the repo-facts probe on the user's three largest real DNGs measured **8064×6048 = 48.8 MP** embedded previews (see §9).

Running variance-of-Laplacian on the draft buffer would compare a 504×378 iPhone JPEG against a 4032×3024 iPhone HEIC **of the same moment** and declare the HEIC vastly sharper. That is a measurement error, not a tuning problem.

**Decision: fix the working resolution at 1024 px on the long edge, aspect-preserving, always resampled *down* with a box/area filter, re-drafted from the bytes already in RAM.** Never up-sample a frame that arrives smaller — flag it and exclude it from cross-frame sharpness comparison. Rationale: any smooth kernel (Lanczos, bicubic) has its own frequency response and would itself alter high-frequency energy, so box averaging is the most neutral choice; up-sampling manufactures no detail. On the value 1024: digiKam's own constant is `size = 512` ("Size of squared original image", `imagequalityparser_p.h:65`), but digiKam scores *absolute* quality for accept/reject banding whereas this task ranks near-identical frames, where differences are finer. Camera-shake and missed-focus differences within a burst live in the 1–3 px blur-radius range at native resolution; at the 1/8 scale the current draft gives a 24 MP frame, a 2 px radius becomes 0.25 px and is gone. **The specific value 1024 is a reasoned starting point, not a validated one** — Phase 2 sweeps 512 / 1024 / 2048 against the user's own burst pairs. No paper fixing a working resolution for burst ranking was found.

**Cost of the second decode, per format.** JPEG: cheap — re-`draft()` at `(1024,1024)` instead of `(256,256)` from the same `bytes` already in memory, so **no second disk read and no second NAS round-trip**; on a 24 MP frame that is 1/4 rather than 1/8, roughly 4× the current decode work, not 64×. HEIC: no saving available — it already decodes full-size, so the metric tier adds no extra decode but the existing decode is the expensive one (whether `pillow_heif` can decode at reduced scale by another route is **not verified**). RAW: the embedded preview is already what gets decoded; `rawpy.RawPy.extract_thumb` is verified present in rawpy 0.26.1 and is the right call for the preview's true size — a full `postprocess()` demosaic is not warranted for a ranking signal. PNG/WebP: already full-size, downsample in numpy.

**The split that matters for cost:** exposure (#23-27), colour (#29-32), contrast (#36-38), histogram distance (#47) and mean colour (#50) are histogram/moment statistics, near scale-invariant, and run **free on the existing draft buffer**. All sharpness, blur, noise and NR-IQA signals (#1-22, #33-35, #43-45) need the fixed scale and therefore the second decode.

## 1e. Funnel — 67 %, and it is bad news for "the expensive tier is rare"

Measured on the user's real manifest `migration_manifest.sqlite` (34.6 MB, 2026-06-19, 40 774 rows), read through `sqlite3.connect("file:<path>?mode=ro&immutable=1", uri=True)` via `scratchpad/funnel.py`, `reconcile.py`, `burst_sql.py`:

| Tier definition | rows | % of 40 774 |
|---|---|---|
| dup-group members (`group_id` shared by ≥2 rows) | 24 531 | 60.16 % |
| **dup ∪ burst ≤ 3 s** | **27 347** | **67.07 %** |
| dup ∪ burst ≤ 10 s | 32 168 | 78.89 % |
| burst ≤ 3 s not already in a dup group | 2 816 | 6.91 % |
| burst ≤ 10 s not already in a dup group | 7 637 | 18.73 % |

**Restricting expensive per-pixel work to dup-group members and burst candidates removes only 33 % of the library at a 3 s window, and 21 % at 10 s.** Bursts add very little on top of grouping — 2 816 extra rows — because most burst frames are already near-duplicates and already grouped. **Per-image cost must therefore be judged against full-library scan time, not against a small tail.**

Corroboration: the independent `run-manifest.sqlite` (20.9 MB, 2026-06-08, 39 612 rows) gives 67.32 % at 3 s and 78.83 % at 10 s — **the two manifests agree to within 0.3 pp**, so the funnel fraction is a stable property of this library rather than an artefact of one scan.

Supporting numbers: `shot_date` coverage 35 524 = **87.12 %**, every sample `len=19` (whole seconds, no sub-seconds, no timezone), 0 unparseable. Extension mix: jpg/jpeg 22 898 (56.16 %) · heic 10 683 (26.20 %) · png 2 439 (5.98 %) · dng 2 356 (5.78 %) · mov 1 940 (4.76 %) · mp4 417 (1.02 %) · other 41 (0.10 %); **no non-DNG raw at all**. Group sizes: 8 365 groups, 56.37 % of size 2, 17.80 % of size 3, 0.60 % larger than 10. Burst runs (consecutive ≤3 s gaps): 7 161 runs of size ≥2, 60.56 % of size 2, largest run 88. **Both distributions are dominated by pairs — a moment feature buys most of its value on 2- and 3-file clusters.**

Two caveats the numbers carry. First, camera identity is **ignored** in the burst query because `make`/`model` are not persisted (§1a #21), so the burst figures are an **upper bound** — two cameras firing in the same second count as a pair. Second, an instrument note that was resolved: a first version used `julianday(shot_date) * 86400.0` and returned 20 757 rows at ≤3 s instead of 20 916, because `julianday` is a double near 2.46e6 so `×86400` carries ~4.7e-5 s of absolute error, pushing gaps of exactly 3 s over the threshold. **The integer `strftime('%s')` form is exact** and agrees to the row with an independent Python LAG/LEAD implementation.

---

# 2. Cost table — per KEEP signal

Every local timing cell is **TBD Phase 2** by construction: no ms/image figure for any classical operator was found in the literature, and every learned figure is on hardware that is not this rig. The MB column is the literature/registry figure with its URL.

| Signal | Tier | ms/image local | Scan-time delta on NAS | ms (lit.) + hardware | MB (source) |
|---|---|---|---|---|---|
| SubSec / Offset / BurstUUID / ContentIdentifier / Make-Model-Lens / ISO-Exp-FNum-Focal / seq counters | FREE | TBD Phase 2 | TBD Phase 2 | — | **0** — same exiftool call, no extra process, no extra file open (§7) |
| Colour histogram | T0 | TBD Phase 2 | TBD Phase 2 | — | 0 (PIL, installed) |
| Loose pHash via BK-tree | T0 | TBD Phase 2 | TBD Phase 2 — expected ~0, no second file read | — | 0 (already computed and persisted) |
| Tile-topk variance of Laplacian | T0 | TBD Phase 2 | TBD Phase 2 | none found | 0 (numpy/scipy, installed) |
| Clipping % per channel | T0 | TBD Phase 2 | TBD Phase 2 | none found | 0 |
| Crete–Roffet re-blur | T0 | TBD Phase 2 | TBD Phase 2 | none found | 0 |
| Structure-tensor anisotropy | T0 | TBD Phase 2 | TBD Phase 2 | none found | 0 |
| MAD-wavelet noise sigma | T0 | TBD Phase 2 | TBD Phase 2 | none found | 0 (`pywt` present transitively; an explicit `PyWavelets` requirement line, not a download) |
| **The 1024 px re-decode these five need** | T0 | TBD Phase 2 | TBD Phase 2 | — | 0 — but ~4× the current JPEG decode work (§1d) |
| DIS optical flow (burst dynamics) | T1 | TBD Phase 2 | TBD Phase 2 — per adjacent-frame *pair*, bursts only | none found | **43.8** (opencv-python-headless wheel, https://pypi.org/project/opencv-python-headless/#files ) |
| YuNet face detection | T2 | TBD Phase 2 | TBD Phase 2 | **0.69 ms @160×120, i7-12700K** — cost at 320×320+ is unknown | **0.23** (232,589 B, https://huggingface.co/api/models/opencv/opencv_zoo/tree/main/models?recursive=true ) |
| FER expression | T2 | TBD Phase 2 | TBD Phase 2 | **1.79 ms @112×112, i7-12700K** — *per face crop* | **4.79** (4,791,892 B; int8 1.36) |
| Eyes-open (MediaPipe blendshapes) | T2 | TBD Phase 2 | TBD Phase 2 | unknown | **bundle unknown**; wheel **20.1** (https://pypi.org/project/mediapipe/#files , Python 3.9–3.12 only) |
| NIMA-class aesthetic | T2 | TBD Phase 2 | TBD Phase 2 | unknown; digiKam's InceptionV3 aesthetic path measured **5000 images in 527 s ≈ 0.105 s/image** vs 863 s for its four-detector classical path (https://phuockhanhle.github.io/jekyll/update/2022/06/19/gsoc-2022.html ) | **13.96 proxy** (zoo MobileNetV2, 13,964,571 B) — **no canonical NIMA weight file exists** |
| Embedding cosine (DINOv2 ViT-S/14, quantised + pinned) | T3 | TBD Phase 2 | TBD Phase 2 | unknown | **~84** — *arithmetic from 21M params at fp32, not a published file size* (https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md ) |
| *Runtime* onnxruntime | T2 prerequisite | — | — | — | **14.0** (https://pypi.org/project/onnxruntime/#files ) |

**Phase 2 must produce, per row: probe path + SHA + args + JSON output.** Two structural facts the measurements must respect. (a) The compute runs on a worker thread (`compute_pool = ThreadPoolExecutor(os.cpu_count() or 4)`, `core/app_service/scan_runner.py:931`) or a worker process (`core/app_service/scan_runner.py:844`), selected by `scan.hash_pool` whose live value on the user's rig is `"auto"` — never on the GUI thread. (b) On the process path a new signal must return as plain picklable data; a numpy array is picklable but pays a full copy per file across the boundary.

---

# 3. Validation — the metric contract (Phase 1 placeholder)

Nothing here is measured. This section fixes **what** Phase 1 must produce so Phase 2 has something to score against.

**Label source:** `qa/fixtures/visual-gt.csv`, gitignored. It holds the human's own picks over the user's own photos. It is not committed because it names real file paths in a private library.

**Per-label fields the ground truth must carry**, because three of the five metrics below cannot be computed without them: the group or moment key, every candidate path in that group, the human's chosen winner, the human's **confidence** (`clear winner` vs `toss-up`), and the human's free-text tag for the *reason* (blur / eyes / expression / exposure / composition / other).

**The five metrics:**

1. **Top-1 agreement per group** — the fraction of groups where the model's argmax equals the human's pick. This is the headline number and the one comparable to published work: the 2018 light-head adversarial network reports **top-1 64.1 % / top-3 86.2 %** over ~11-frame bursts (https://arxiv.org/pdf/1803.07212), so a candidate that cannot beat 64 % on this library is not competitive with a 0.47 MB model from 2018.
2. **Pairwise concordance** — over all within-group pairs, the fraction where the model's ordering matches the human's. Less brittle than top-1 on large groups, and it degrades gracefully when the human declined to rank beyond a winner.
3. **Rank correlation** (Spearman or Kendall τ) per group, then aggregated. Catches a model that finds the winner but orders the rest randomly.
4. **Per-case breakdown by the user's own reason tags.** A model at 70 % overall that is 40 % on the eyes-closed cases has a *different* verdict from one that is uniformly 70 %. This is the metric that decides which tier is required, because eyes and expression are precisely the signals classical metrics cannot reach.
5. **Agreement conditioned on the human's confidence.** Agreement on `clear winner` groups and on `toss-up` groups are separate numbers and must be reported separately. **This is the metric that sets the autonomy band** — see §5.

**Contract note.** A metric that is only reported in aggregate hides exactly the failure mode this feature would ship with. All five are reported, or none of them is evidence.

---

# 4. Pareto frontier (Phase 2 placeholder)

The frontier is plotted once Phase 2 has numbers. Its axes are fixed now so the measurements are collected in a comparable shape.

- **X — cost:** added wall-clock per full-library scan on the user's real topology, in seconds, measured on **D + H + J** (never a convenience subset; the retro's rule N2). Reported alongside the per-image ms and the added MB of dependencies, which are separate cost dimensions and must not be collapsed into one axis.
- **Y — accuracy:** top-1 agreement per group from §3, with the confidence-conditioned split shown as two points, not one.
- **Third dimension, shown as the marker, not an axis — dependency footprint:** FREE / T0 / T1 (+43.8 MB) / T2 (+~33 MB) / T3 (+~84 MB). A point that is Pareto-optimal on cost and accuracy but sits at T3 may still lose to a T0 point, because the install is a gated action on this machine and the download is a permanent product cost.
- **Reference points to plot for scale:** the funnel's 67 % (a signal restricted to the funnel saves only 33 % of the cost, §1e), and digiKam's measured 0.105 s/image aesthetic path against its own 0.173 s/image classical path (https://phuockhanhle.github.io/jekyll/update/2022/06/19/gsoc-2022.html ).

Each plotted point carries its 4-tuple. A point without one is not on the chart.

---

# 5. Autonomy — the prior, and what Phase 2 must add

## 5.1 The prior: 14 products surveyed, zero auto-delete on a visual score

This is the strongest single finding of Phase 0 and it is available now, before any measurement.

| Autonomy level | Products | What it actually does |
|---|---|---|
| **Deletes automatically on a quality score** | **none** | — |
| Deletes on user command, recoverable | Apple Photos (Merge → Recently Deleted, **30 days**, https://support.apple.com/guide/photos/remove-duplicates-pht5a3157c1d/mac ), immich (`resolveGroup` → trash, requires explicit `keepAssetIds` **and** `trashAssetIds`, https://raw.githubusercontent.com/immich-app/immich/main/server/src/services/duplicate.service.ts ), Czkawka (mechanical select rule, user presses delete) | Always a separate, explicit human act on a reviewed group — and always to a bin, never `unlink` |
| Auto-selects a winner and **hides** the rest | Google Photos Stacks (https://support.google.com/photos/answer/14169846 ), Lightroom Assisted Culling, PhotoPrism Stacks (https://docs.photoprism.app/user-guide/organize/stacks/ ), ON1 Stacking (https://www.on1.com/products/photo-raw/features/ ), Aftershoot (https://support.aftershoot.com/en/articles/6508163-setting-your-ai-automated-culling-preferences-in-aftershoot ) | The group collapses to one cover image. Nothing leaves disk. Google says explicitly it "doesn't change your available storage" |
| Ranks / flags only, no view change | digiKam Pick Label red/yellow/green (https://docs.digikam.org/en/maintenance_tools/maintenance_quality.html ), Narrative Select 0–100 focus + labels (https://narrative.so/select ), Excire Foto (https://excire.com/en/excire-foto/ ), FilterPixel "Best of Burst" + a written reason (https://filterpixel.com/deepcull ) | Writes a label; the user filters on it |
| No automation at all | Photo Mechanic, Corel AfterShot Pro (https://learn.corel.com/quick-photo-workflow-aftershot-pro/ ), darktable (https://docs.darktable.org/usermanual/development/en/lighttable/digital-asset-management/grouping/ ) | The professional incumbents. Manual rating only |

**The strongest autonomy anyone grants a pixel-quality model is hiding a photo behind a stack cover, and that is always reversible in one click.** Where deletion happens at all it is (a) on duplicate/near-duplicate *identity* rather than aesthetic judgement, (b) initiated per-batch by a human who saw the group, and (c) recoverable for weeks. Note the desktop AI culling tools are the maximum-pressure case — their users are professionals with thousands of near-identical frames — and **none of them deletes**.

## 5.2 Confidence-margin mechanisms actually shipped

| Mechanism | Product | Detail |
|---|---|---|
| **Explicit abstain band** | **digiKam** | `pending` occupies **10–60** of a 0–100 score |
| Explicit abstain state | Lightroom | eyes-open returns **"Can't tell"**, not a forced boolean (https://adobe.design/ideas/behind-the-design-assisted-culling-in-adobe-lightroom ) |
| Explicit abstain bucket | Aftershoot | **"Maybe Photos"**, surfaced only at the strictest culling level |
| Two-tier bar: detect low, act high | PhotoPrism faces | `FACE_SCORE` **50–65** to detect, `FACE_CLUSTER_SCORE` **60–85** to be clustered automatically (https://raw.githubusercontent.com/photoprism/photoprism/develop/internal/ai/face/README.md ) |
| Near-tie margin | PhotoPrism faces | `FACE_MATCH_MARGIN` **0.01** — refuse to decide a coin toss |
| Per-signal user threshold | Lightroom, Aftershoot, Czkawka, immich | No fixed cut ships enabled by default |
| Opt-in by default | digiKam (`enableSorter = false`), Google Photos (a settings toggle), Lightroom (Catalog Settings), immich stacking (manual) | The feature is off, or manual, until the user turns it on |

**digiKam's exact numbers and paths**, because it is the only project publishing a complete on-device CPU-only pipeline end to end. Defaults, from the `imagequalitycontainer.cpp` constructor (legacy path `core/libs/dimg/filters/imgqsort/imagequalitycontainer.cpp:44-50`, https://raw.githubusercontent.com/KDE/digikam/master/core/libs/dimg/filters/imgqsort/imagequalitycontainer.cpp ): `detectBlur` / `detectNoise` / `detectCompression` / `detectExposure` all `true`; `blurWeight` = `noiseWeight` = `compressionWeight` = **100** each (equal before normalisation); `rejectedThreshold` **10**; `pendingThreshold` **40**; `acceptedThreshold` **60**; `speed` **1**; `enableSorter` **false**. Related constants in `imagequalityparser_p.h:64-65`: `clusterCount = 30`, `size = 512`.

Label mapping (`imagequalityparser.cpp` ~L0209-0235, https://lxr.kde.org/source/graphics/digikam/core/libs/imgqsort/imagequalityparser.cpp ): `finalQuality == 0.0` → `NoPickLabel` (algorithms did not run); `< rejectedThreshold` → `RejectedLabel`; between → `PendingLabel`; `>= acceptedThreshold` → `AcceptedLabel`. **The dead band from 10 to 60 lands in "pending" — the majority of a real library is expected to be un-judged.** *(Both Phase 0 reports state the band as 10–60 and both give `pendingThreshold = 40`; neither explains what the 40 does. Recorded as-is, unresolved.)*

Aggregation worth copying (`imagequalitycalculator.cpp`, https://lxr.kde.org/source/graphics/digikam/core/libs/imgqsort/imagequalitycalculator.cpp ): normalise the weights, sum `score × weight` into a `damage` term, return `(1 - damage) × 100`; **`adjustWeightByQualityLevel()` multiplies a detector's weight by a penalty factor of 20.0 once its damage score exceeds 0.9** — a soft veto so one catastrophic defect dominates instead of being averaged away; and `if (!numberDetectors()) return -1.0` — **no detectors ran means "no score", not "score zero"**.

Three independent products converged on an explicit abstain output (digiKam's pending band, Lightroom's "Can't tell", Aftershoot's "Maybe Photos"), and Adobe's stated design principle is literally **"Assist, don't decide"**.

## 5.3 What Phase 2 must add before any autonomy verdict

The prior above is prior-art evidence. It cannot license an auto-select in *this* app. Four measurements are required:

1. **Per-signal error rate**, on the user's own labels, split by the reason tags of §3 metric 4.
2. **Top-1 match rate against the human**, reported separately for `clear winner` and `toss-up` groups (§3 metric 5).
3. **The margin at which disagreement with the human drops below X** — i.e. the score gap between rank 1 and rank 2 at which agreement crosses an acceptable rate. X is a product decision the user makes, but the *curve* is a measurement. This is the direct analogue of PhotoPrism's `FACE_MATCH_MARGIN = 0.01` and it is what an abstain band would be built from.
4. **The cost of the failure mode.** Not the error rate but its consequence: what happens when the app picks wrong, whether that is recoverable, and how long the user has to notice. photo-manager's existing gates are the relevant surface — the #536/#540 duplicate-action allowlist and the #517 low-confidence exclusion (`core/services/auto_select.py:225-232`) sit exactly where Apple and immich sit, and any new `visual_score` that could influence deletion must be measured against them rather than around them.

---

# 6. Verdicts (Phase 2 placeholder — all rows pending)

| Capability | BUILD / DEFER / DO-NOT-BUILD | Justifying measurement |
|---|---|---|
| `moment_id` from free EXIF signals (SubSec, BurstUUID, ContentIdentifier, seq counters, Make/Model/Lens, settings continuity) | pending Phase 2 | pending Phase 2 |
| `moment_id` refinement by loose pHash via the existing BK-tree | pending Phase 2 | pending Phase 2 |
| `moment_id` refinement by colour histogram | pending Phase 2 | pending Phase 2 |
| `moment_id` refinement by embedding cosine (T3) | pending Phase 2 | pending Phase 2 |
| `visual_score` — classical sharpness core (tile-topk VoL, Crete re-blur, structure tensor) | pending Phase 2 | pending Phase 2 |
| `visual_score` — exposure (clipping %) and noise (MAD-wavelet) | pending Phase 2 | pending Phase 2 |
| `visual_score` — burst dynamics via DIS optical flow (T1) | pending Phase 2 | pending Phase 2 |
| `visual_score` — face detection + expression (T2) | pending Phase 2 | pending Phase 2 |
| `visual_score` — eyes-open (T2, licence-clean route TBD) | pending Phase 2 | pending Phase 2 |
| `visual_score` — NIMA-class aesthetic | pending Phase 2 | pending Phase 2 |
| BRISQUE / NIQE | pending Phase 2 — **measure once, expect DO-NOT-BUILD** | out-of-distribution on real camera output (§1b #43/#44) |
| Auto-select autonomy level (rank / hide / pre-tick / delete) | pending Phase 2 | §5.3 items 1-4 |

---

# 7. If BUILD — the constraints that already exist

No schema is proposed here. These are the facts a design must satisfy, each read on the branch.

**`_MIGRATIONS` is ADD COLUMN only, append-only, idempotent.** `infrastructure/manifest_repository.py:84-105` — 14 entries, identical to master. Every entry is a bare `ALTER TABLE … ADD COLUMN <name> <ddl>`; re-running is safe because SQLite raises on a duplicate column and the runner swallows it. Nullable columns carry **no** `DEFAULT`; `NOT NULL` columns carry an explicit one, because SQLite requires it for `ADD COLUMN NOT NULL`. Order is load-bearing: appending is the only safe edit. There is exactly one non-ADD-COLUMN migration in the repo's history (the #433 `dest_path` drop, handled by a copy-table dance at `infrastructure/manifest_repository.py:147` on the branch). Adding a column takes **three companion edits**: the migration entry, the canonical `CREATE TABLE` DDL (`scanner/manifest.py:12-34`), and the INSERT column list (`scanner/manifest.py:42-47`) plus its values (`scanner/manifest.py:134-147`). The repo has a skill that enforces exactly this (`sqlite-migration-safety`).

**The exiftool slot.** `_read_extract_chunk` builds an explicit tag list at `scanner/exif.py:774-794` and calls one `-stay_open` process launched at `scanner/exif.py:211-218` with a bare `"exiftool"` on `PATH`. Adding N more `-TAG` selectors costs a longer stdin line and a larger JSON payload — **the same call, no extra process, no extra file open** — because exiftool reads each file once per `-execute` regardless of how many selectors filter the already-parsed metadata block. There is no `-fast` on this path (deliberate, so MakerNotes and XMP past the first IFD are scanned), which is also what makes Apple's `BurstUUID` / `ContentIdentifier` reachable. One trap: key names in `_record_to_extract` (`scanner/exif.py:825-869`) must match the `-G` group-0 form (`MakerNotes:` / `QuickTime:`) or `rec.get()` silently returns `None`.

**The PIL slot — and the branch's JPEG bypass.** On this branch JPEG no longer goes through exiftool at all. `_INMEMORY_EXIF_TYPES = frozenset(("jpeg",))` at `scanner/hasher.py:259`; `extract_pil_scoring_signals` runs in the hash worker at `scanner/hasher.py:176-177` (defined at `scanner/exif.py:636`) and parses PIL EXIF/XMP **by numeric tag id** via `_PIL_CENSUS_TAG_IDS` (`scanner/exif.py:519`); `core/app_service/scan_runner.py:720` then routes any outcome carrying `inmemory_signals` straight into `extracts` and never enqueues it. **56.16 % of the user's library is JPEG (§1e), so on this branch a new EXIF-derived signal needs two implementations** — an exiftool key lookup for HEIC/RAW/video and a PIL numeric-tag lookup for JPEG — **and Apple MakerNotes tags are not reachable from `PIL.Image.getexif()` at all**, so a JPEG `BurstUUID` signal would either force JPEG back onto exiftool or need a different parser. HEIC deliberately stays on exiftool (`pillow-heif` exposes no `xmp` info key). `merge_extracts()` no longer exists on this branch.

**The `hasher.py` insertion point.** `scanner/hasher.py:194-206`, inside `_hashes_from_data` (`scanner/hasher.py:120`), in the `try:` block after the `img is None` guard at `:193`. At that point `img` is an already-decoded, already-`convert("RGB")`-ed, already-`load()`-ed PIL image (`scanner/hasher.py:187-189`), and `imagehash` is itself numpy-backed, so `numpy.asarray(img)` there is a view, not a new decode. Any change to the per-file recipe must bump `HASH_RECIPE_VERSION` (`scanner/hasher.py:108`, currently `"3"`) or the #486 auto-pool calibration cache mis-projects.

**`rescore()` is I/O-free, and that is why a new signal must be persisted.** `infrastructure/manifest_repository.py:564` — confirmed zero file I/O: the only external calls are `_connect`, one `SELECT` (`:604`), `score_group`, and one `executemany` UPDATE (`:656`). It reads `source_path, action, group_id, pixel_width, pixel_height, file_size_bytes, shot_date, mtime, exif_tag_count, gps_present, xmp_derived WHERE group_id IS NOT NULL`. **A signal that is computed but not persisted is invisible to `rescore()` and forces a full re-scan for every weight change** — that is the cost of the current `match_confidence` design (transient, never written to the DB, `scanner/dedup.py:174`) and it is the trap to avoid. To participate, a new signal must be a persisted column, be added to that `SELECT`, be threaded into the `ManifestRow` rebuild, and be read by the scorer under a weight key in `DEFAULT_WEIGHTS` (`scanner/scoring.py:89`), whose `validate_weights` (`scanner/scoring.py:217`) requires the weights to sum to 1.00. Note `WHERE group_id IS NOT NULL`: on the user's live manifest `rescore()` touches 24 531 of 40 774 rows.

**The two auto-select gates a new score must not disturb**, both in one set comprehension at `core/services/auto_select.py:225-232`: the #536/#540 duplicate-action allowlist (`_DUPLICATE_ACTIONS = frozenset({"EXACT", "REVIEW_DUPLICATE"})`, `core/services/auto_select.py:194`) and the #517 low-confidence exclusion (`getattr(row, "match_confidence", None) != "low"`, so `None` passes). Folding a visual signal into the existing `score` float means re-balancing all eight existing weights, which changes every keeper pick in the library; a parallel `visual_score` column passes both gates trivially because neither gate reads it, but then nothing consumes it either. That trade is a Phase 2 decision, not a Phase 0 one.

---

# 8. Not verified — consolidated

Every "could not verify" from all five Phase 0 reports, attributed. Nothing here was guessed at.

**From `phase0-grouping-census.md`:**
1. `exiftool.org` is unreachable from this environment — every `TagNames/*.html` URL returns HTTP 404 and the fossies mirror returns 401. All tag names were verified against the exiftool Perl source instead, which generates those HTML pages.
2. No Samsung burst identifier is exposed by exiftool (two commented-out undecoded blobs only). Samsung has a burst-name patent (https://patents.google.com/patent/US9071735 ) but no filename convention could be confirmed.
3. No DJI or GoPro burst/sequence tag exists — `grep -c Burst` returns 0 in both modules.
4. **HEIF burst/sequence containers** — no evidence found that Apple stores an iPhone burst as a single HEIF image-sequence container. The observed mechanism is N separate files sharing `BurstUUID`. Flagged as unverified, not answered.
5. Google Photos' actual moment thresholds are not public. The nearest primary source is the patent https://patents.google.com/patent/US9411831B2/en , which describes a data-dependent threshold.
6. **No cost figure in that document was measured.** Only two numbers have a hard artifact behind them: the MobileCLIP-S0 checkpoint size (HF file metadata) and its published latency (Apple's README, iPhone hardware).

**From `phase0-selection-classical.md`:**
7. **No literature ms/image figure was found for any classical signal.** The one primary cost statement is digiKam's own code comment that its k-means noise detector needs a 256×256 crop "to speed-up computation time" — qualitative, not a number.
8. Whether `scipy.ndimage`, `numpy.fft` and `PyWavelets` release the GIL. No authoritative documentation statement was found for any of the three.
9. Whether Adobe's Assisted Culling runs locally or in the cloud. "Best Photos" is separately identifiable as a Lightroom CC (cloud) feature; Assisted Culling is a different feature and could not be confirmed.
10. Apple Photos and Google Photos selection signals — neither publishes them.
11. Photo Mechanic quality signals — searches returned nothing indicating it does sharpness or quality analysis at all, but no vendor page *denying* it was found either.
12. NIQE/PIQE availability in OpenCV. BRISQUE was confirmed in `cv2.quality` with its two model files, but the OpenCV quality-module doc page is behind a Cloudflare challenge (HTTP 403 to both WebFetch and curl), so the module's full class list is unverified.
13. Whether `pillow_heif` can decode HEIC at reduced scale by some route other than `draft()`. Only verified that it does not override `draft`.
14. Signals #28 (HDR-ness) and #32 (inter-frame WB consistency) — no classical metric paper found for either; both are constructions.
15. **The 1024 px working-resolution value is reasoned, not validated.** The measurement establishing that a *fixed* resolution is required is solid and reproducible via `scratchpad/draft_ladder.py`; the specific value needs a Phase 2 sweep.
16. digiKam's `size = 512` constant — its declaration and comment were read, but not every use traced, so it cannot be stated with certainty that it is the resize target applied before the detectors run.

**From `phase0-selection-learned.md`:**
17. **MediaPipe Face Landmarker bundle size, and whether its 52 blendshapes include `eyeBlinkLeft/Right` and `mouthSmileLeft/Right`.** Flagged by its own author as the single most important unverified item, because it decides whether a licence-clean eyes-open path exists off the shelf.
18. BlazeFace short-range `.tflite` file size — not found.
19. ULFD licence — the README states sizes and timings but no licence was found.
20. NIMA MobileNet — no canonical released weight file, so no true size and no CPU ms. The 13.96 MB figure is the zoo's MobileNetV2 classifier used as a size *proxy*, not a NIMA model.
21. CPU ms for almost every model outside the OpenCV Zoo table: SCRFD at any size, ArcFace/MobileFaceNet, CLIP ViT-B/32, DINOv2, MoveNet, U2-Netp on named x86 hardware, all NR-IQA models, MobileSAM, RTMPose-t.
22. MobileSAM ONNX file size and confirmation of its Apache 2.0 licence.
23. MoveNet's licence from a primary Google model card. RTMPose-t size and latency; RTMPose/MMPose licence from a primary source.
24. `pyiqa` / IQA-PyTorch repository licence; LAION aesthetic predictor licence and exact weight sizes.
25. Whether the `torch` Windows PyPI wheel bundles CUDA or is CPU-only; `ncnn` Windows wheel size and licence.
26. **Installed-on-disk sizes for every package.** Only download wheel sizes were obtained.
27. DINOv2 ViT-S/14 file size in MB — the ~84 MB figure is arithmetic from 21M params at fp32.
28. PhotoPrism's exact SCRFD 0.5g file size from a primary listing, and whether PhotoPrism has addressed the InsightFace weight licence.
29. **Excire / Aftershoot / Narrative technical claims** — sourced only from vendor and competitor marketing pages; **no architecture, model size or licence information is public for any of them.** (Recorded separately from the prior-art report's own vendor-page gaps below, because the learned census reached the same wall from the model side.)

**From `phase0-prior-art.md`:**
30. Adobe's official Assisted Culling help pages both **timed out at 60 s, twice** — the slider defaults and the auto-stack default time interval are unknown as a result.
31. `aftershoot.com/culling-faq/` returned **HTTP 403** — the "will never delete images unless you do it yourself" quote reaches us only through a review site quoting the vendor.
32. Apple's merge pages returned navigation only, not article body; only the Recently-Deleted/30-days fact came back from an Apple URL.
33. Apple's duplicate-detection and burst-suggestion algorithms — no vendor technical documentation exists. **Do not fill this gap with NeuralHash**: that is the withdrawn CSAM client-side-scanning system, a different subsystem with no published connection to the Duplicates album.
34. Google Photos Stacks thresholds — neither the similarity metric nor the "short time frame" is published. Google Photos library-wide duplicate detection rests on `[secondary]` sources only.
35. Top Shot model size and latency — architecture and attribute list published, no MB, no parameter count, no ms.
36. immich's `suggestDuplicateByFileSize` — the *rule* (prefer larger files, prefer more metadata) is from immich's own documentation and is solid; the **function name and its implementation** are `[secondary]` and the file path is unknown.
37. digiKam's noise, compression and exposure detector internals — only `imagequalityparser_blur.cpp` was read in full.
38. Czkawka's GUI auto-select rules — the core crate was read, not `czkawka_gui` / Krokiet; the "Select all except biggest / oldest" list is `[secondary]`.
39. Narrative Select: on-device or cloud — no vendor statement found either way. FilterPixel: **the vendor's own pages contradict each other**; unresolved. ON1 and Corel AfterShot Pro: marketing and review pages only, low confidence. Excire "X-tetics" model name appears in a review, not on the vendor page fetched.
40. digiKam's `speed = 1` default is confirmed but what its values *mean* is unknown.
41. Recorded so it is not re-investigated: US patent 12,067,047 B2 surfaces high in searches for Apple's duplicate detection but its assignees are "Dyna LLC" and "Cooper Harris Trust" — **not Apple**, and unrelated to any product in this survey.

**From `phase0-repo-facts.md`:**
42. Whether `origin/docs/web-port-feasibility` at tip `e9e7471` is current. The ref was ~6 weeks old at the time of that pass and no fetch was permitted; all branch findings are as of that commit.
43. Where the web port's HTTP surface lives. The branch tree has no `api/`, `web/` or `server/` directory and no route definitions were located.
44. Why `migration_manifest.sqlite` has **zero scoring signals** (every `exif_tag_count` and every `score` is NULL) while the older `run-manifest.sqlite` has them. Plausible causes are an exiftool-missing run or a scan interrupted after hashing; it would need the app log from that run. It does not affect the funnel numbers, which use `group_id`, `shot_date` and `source_path` only — but **that DB cannot be used to study the existing scorer**.
45. Whether `xmp_derived` is genuinely always false (0 of 79 386 rows across two scans) or the `XMP-xmpMM:DerivedFrom` selector never matches under `-G` group-0 naming. The code itself hedges. Needs one file known to carry the tag, run through the live pipeline.
46. HEIC and non-DNG-RAW real decode sizes. HEIC was measured only on a synthetic 4000×3000 file — the structural fact (no `draft()`, full-resolution decode) is settled, but no real HEIC from the library was timed, and the library contains no non-DNG raw at all.
47. Actual per-file wall cost of the exiftool pass vs the hash read on this rig. The branch cites 1.9–8× from `docs/audits/nas-probe-results-2026-07.md` at `scanner/exif.py:498`; that file was not read or re-measured in the Phase 0 pass.

---

# 9. Follow-ups noticed, not in scope

1. **The RAW path hashes a 48.8 MP embedded JPEG, and opens LibRaw twice per file.** `_hashes_from_data` calls `_load_raw_preview_from_bytes(data)` at `scanner/hasher.py:148` (defined `:483`, `extract_thumb` at `:503`) and then opens LibRaw **again** for `raw.sizes` at `scanner/hasher.py:161` (with a path-based fallback at `:165`). Measured on the user's three largest real DNGs, all reachable on `H:` (`scratchpad/raw_probe.py`): each is a 124–129 MB file with an 8064×6048 sensor and a **full-resolution embedded JPEG preview of 21–22 MB**, so the hashes run on **48.8 MP** — 260× the pixel count of a 12 MP JPEG after `draft()`. Wall time for the two LibRaw opens alone was 0.339–0.383 s per file with the bytes already in RAM. RAW is by far the most expensive pixel path in the pipeline today, and DNG is 5.78 % of the library. The in-code comment at `scanner/hasher.py:155` still says thumbnails are "typically low-res previews (e.g. 1024×768 for a 12 MP DNG)", which is not what iPhone ProRAW does.
2. **`shot_date` is truncated to whole seconds at parse time, unconditionally.** `parse_exif_date` (`scanner/exif.py:395-404`) does `raw = raw[:19]` at `:400` then `strptime(raw, "%Y:%m:%d %H:%M:%S")` — sub-second digits and any timezone suffix are sliced off *before* parsing, so `datetime.isoformat()` always emits a 19-character string. Confirmed against the real DB: 35 524/35 524 dated rows parse, every sample `len=19`. Sub-second ordering is therefore unavailable at every layer today — not requested from exiftool, discarded by the parser, and absent from the persisted column.
3. **`xmp_derived` is 1 on zero rows across two independent scans** (0 of 79 386). Either the library genuinely holds no Lightroom/Photoshop derivatives, or the selector never matches under `-G` group-0 naming; the code hedges at `scanner/exif.py:487-489`. One file known to carry `xmpMM:DerivedFrom`, run through the live pipeline, would settle it.
4. **`xmp_rating` was parsed and never consumed on master** — no manifest column, no scoring weight. The branch deleted it entirely (#786). Recorded because it is the shape of the trap §7 warns about: a computed-but-unpersisted signal.
5. **The `photo-scanner-patterns` skill describes a tiered exiftool ("only for HEIC/RAW/MOV/MP4") that does not match the code.** On master every non-`skip` record goes to exiftool; the branch introduces the first real tier, and it is JPEG-*bypass*, not HEIC-only. The skill text is stale in a way that would mislead a fresh session.
