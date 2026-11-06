"""Report lines over 110 characters (code points) and over 110 display columns (CJK counted as 2)."""
import sys
import unicodedata

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    for n, line in enumerate(fh, 1):
        s = line.rstrip("\n")
        cols = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
        if len(s) > 110 or cols > 110:
            print(n, len(s), cols, s[:60])
