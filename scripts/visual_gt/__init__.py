"""Local ground-truth labelling harness for the visual auto-select study.

Two modules, both CLI-invocable:

* :mod:`scripts.visual_gt.bootstrap` — reads the app's manifest DB
  READ-ONLY, derives candidate "moment" groups (duplicate groups + burst
  runs), enriches them with a one-shot exiftool pass, and writes a
  deterministic stratified sample to a JSON sidecar.
* :mod:`scripts.visual_gt.server` — serves that sample on 127.0.0.1 for
  hand-labelling and appends the labels to a CSV.

Neither module ever writes to the manifest, and neither ever touches a
photo file except to read bytes for a thumbnail.
"""
