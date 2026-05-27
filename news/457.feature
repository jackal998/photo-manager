Pipeline hash → exiftool stages via a producer-consumer queue so the two stages overlap; total NAS scan wall time drops by ≈ min(hash, exif) (#450).
