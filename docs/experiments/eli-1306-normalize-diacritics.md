# ELI-1306 — Diacritics and apostrophe normalization

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Problem

The scorer normalized spaces and hyphens but not accented characters or apostrophe variants. Equivalent names could therefore be counted as both a false positive and false negative rather than one true positive.

## Correction

```python
s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
for curly in "‘’ʼ′":
    s = s.replace(curly, "'")
```

NFKD decomposes accented characters into base characters and combining marks where Unicode defines that decomposition. Removing combining marks handles those accents; it does not transliterate all Unicode text into ASCII. Apostrophe mapping makes several typographic forms comparable while preserving the existing space/hyphen behavior.

## Historical verification

Three documented cases were corrected: two accented/unaccented spelling pairs and one straight/curly apostrophe pair. A regression check retained the previous space/hyphen behavior. The historical suite reported 335 passing tests and no regressions.

## Boundary

An appended abbreviation and a US/UK spelling variation remained outside this transformation's scope. Those require alias or fuzzy matching, not accent removal. The authentic entity-name pairs are omitted rather than replaced with altered quotations.
