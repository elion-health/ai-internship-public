# ELI-1317 — Grounding names in visible text rather than URLs

Public research edition of the historical report. Dataset rows, internal review material and private artifacts are omitted. Commands describe the historical experiment; see ../reproduction.md for the supported standalone setup.

## Failure mode

An extracted endpoint name could appear only inside a hyperlink destination, while visible text described an anonymous organization. The extractor was effectively deriving identity from a URL slug rather than readable evidence.

The correction was deterministic and required no model call:

```python
_contains_endpoint(content.lower(), endpoint) and not _contains_endpoint(
    normalize_text(content).lower(), endpoint
)
```

`normalize_text` collapses Markdown links to visible anchor text. `_endpoint_named_only_via_url` fires only when the name exists in raw content and disappears after normalization. It checks the customer subject or integration object as appropriate before semantic verification.

This is intentionally narrower than requiring every endpoint name to appear visibly. Earlier broad name-presence checks failed on real aliases and shortened forms. If an extracted variant is absent from the entire raw page, this new check does not reject it.

## Why the case count changed

| Stage | Count | Explanation |
|---|---:|---|
| Automated taxonomy | 58 | Included visible names, spelling differences, and other unrelated cases |
| Quote-only inspection | 2 | Missed names present in hyperlinks elsewhere on the page |
| Actual function against fetched content | 9 | Tested the mechanism against its real input |

The nine confirmed cases comprised eight customer endpoints and one integration endpoint. All nine were rejected using actual retained page content. The historical suite reported 336 passing tests, including guards against reintroducing broad name-matching brittleness.

## Interpretation and limitations

The other 49 initial candidates were not this failure mode. Many confirmed cases likely duplicated a relationship correctly sourced from a dedicated page elsewhere, so net set-based F1 impact was expected to be small but was not measured. The value was evidence quality and deduplication.

For a failure involving page-wide location of a name, quote-only inspection was insufficient. Checking the complete input with the intended deterministic rule produced a more reliable scope estimate. Entity identities, URL slugs, and authentic evidence quotations are omitted.
