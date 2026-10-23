> Historical research record. Refer to `docs/reproduction.md` for the public setup, available artifacts, and limits. Original run links and private dependencies are not required. Claims below are historical observations, not current performance guarantees.

# Firecrawl for Healthcare-IT Customer Discovery — Complete Parameter & Configuration Guide

Everything Firecrawl (v2 API, https://api.firecrawl.dev/v2) exposes, every parameter that
matters, and how to configure each one for the specific job of finding paying customers of a
healthcare-IT product from press releases, case studies, vendor sites, logo walls, and review
sites. Verified against the current v2 docs.

---

## 0. Read this first — the likely reason Firecrawl underperformed for you

You reported Firecrawl gave "thin snippets." That is almost certainly a configuration problem, not
a capability ceiling. Three specific causes, all fixable:

**You called `/search` without `scrapeOptions`.** Bare `/search` returns title + URL + a short
snippet. The full page body only comes back when you pass `scrapeOptions` (e.g.
`{"formats":["markdown"]}`) — that's what turns it into search-plus-scrape in one call. Without
it you get exactly the thin snippets you saw.

**`onlyMainContent` defaults to `true`, and it strips exactly where customer logos live.**
"Trusted by" logo walls, footers, client carousels, and nav-adjacent blocks are treated as
boilerplate and removed. For a customer-logo page that is the whole ballgame — you need
`onlyMainContent: false` (and often `includeTags` targeting the logo section).

**You never reached the deep-read step.** Firecrawl's real value for you is not as a primary
search engine (Exa is better at neural recall). It's as the deep-read / structured-extraction
layer on the handful of evidence-dense pages — the vendor customer page, a case-study PDF, a
press release — where 800-char snippets fail. Point it there and it shines.

The rest of this guide is built around using Firecrawl for what it's best at in your pipeline:
**map → deep-read → structured-extract** on high-value pages, with `/search` as a convenience
layer when you want discovery + content in one call.

---

## 1. Which endpoint does which job (for your pipeline)

| Endpoint | What it does | Your use |
|---|---|---|
| `/search` | Web/news/image search, optionally scrapes each result | Discovery pass over press-wire + news; one-call search+content |
| `/map` | Fast URL discovery on one site (sitemap + index + search) | Find the customer/case-study/"trusted by" pages on a vendor's own site — the **single highest-value Firecrawl move** for you |
| `/scrape` | Deep-read one URL → markdown/JSON/etc. | Deep-read the evidence-dense pages map/search surfaced |
| `/extract` | Schema-based LLM extraction across known URLs | Pull a structured customer list from a logo wall or case-study index |
| `/agent` | Autonomous multi-page extraction — you don't supply URLs | "Find customers of product X" as a self-directed job; good baseline to benchmark |
| `/crawl` | Traverse and scrape a whole site/section | Sweep an entire `/customers/` or `/case-studies/` subtree |
| `/parse` | Turn an uploaded file (PDF/Word/etc.) into markdown/JSON | Case-study or press-release PDFs you already have |
| Interact (`/scrape/{id}/interact`) | Drive the browser after scraping (click/scroll/type) | Logo walls behind "load more", filtered customer directories |

**Recommended flow per product:** `/map` the vendor site with `search:"customers OR case study OR
success"` → `/scrape` (or `/extract`) each hit with `onlyMainContent:false` → in parallel,
`/search` the press wires with `scrapeOptions` → feed all evidence to your classifier.

---

## 2. `/search` — full parameter reference

`POST /v2/search`. Cost: 2 credits per 10 results; scraping results adds standard per-page scrape
cost.

| Parameter | Type | Default | Notes for your task |
|---|---|---|---|
| `query` | string | — | Your search string. Supports Google-style operators (`site:`, `OR`, quotes). |
| `limit` | int | 10 | Results to return. |
| `sources` | array | `["web"]` | `web`, `news`, `images`. Use `["news"]` for press-release freshness; combine with `web`. |
| `categories` | array | `[]` | `github`, `research`, `pdf`. `pdf` is useful — many case studies are PDFs. |
| `includeDomains` | array | — | Restrict to hostnames. Pin to `prnewswire.com`, `businesswire.com`, `g2.com`, the vendor domain. Cannot combine with `excludeDomains`. |
| `excludeDomains` | array | — | Drop noisy hosts. |
| `tbs` | string | — | Time filter (web source only): `qdr:h/d/w/m/y`, `sbd:1` (sort by date), or custom `cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY`. Useful to find recent customer announcements. |
| `location` | string | — | Geo string, e.g. `"Chicago,Illinois,United States"`. |
| `country` | string | US | ISO-3166 alpha-2. Keep US for US health systems. |
| `timeout` | int (ms) | 60000 | Per-request timeout. |
| `ignoreInvalidURLs` | bool | false | Drops URLs that would fail downstream Firecrawl calls — turn on if piping search → scrape. |
| `enterprise` | array | `[]` | ZDR options: `["zdr"]` (end-to-end, 10 cr/10 results) or `["anon"]` (anonymized, 2 cr/10 results). Team must be enabled. |
| `scrapeOptions` | object | — | **The parameter you were missing.** Any `/scrape` option (`formats`, `onlyMainContent`, etc.) applied to each result. Without it you get snippets only. |

Rate limits (requests/min): Free 5, Hobby 50, Standard 250, Growth 2,500.

---

## 3. `/scrape` — full parameter reference

`POST /v2/scrape`. Cost: 1 credit base; JSON/extract format +4; enhanced proxy up to +4 (billed
only if the enhanced backend actually runs); PDF 1 credit/page; threat-protection scan +2/URL;
lockdown 5 on hit / 1 on miss.

### 3.1 Content-selection parameters (get these right or you lose logo walls)

| Parameter | Type | Default | Notes for your task |
|---|---|---|---|
| `formats` | array | `["markdown"]` | See §4 — this choice does most of the work. |
| `onlyMainContent` | bool | `true` | Deterministic HTML-level boilerplate strip (no LLM). **Set `false` for customer/logo/footer pages — this is your #1 fix.** |
| `onlyCleanContent` | bool | `false` | Beta LLM pass that removes residual boilerplate while preserving lists/tables/links. Handy on messy news pages; not on ZDR requests. |
| `includeTags` | array | — | CSS selectors/tags to keep. Target the logo grid, e.g. a class on the "trusted by" section. Applied against the original DOM, not the filtered output. |
| `excludeTags` | array | — | Selectors to drop (nav, cookie bars). Also original-DOM. |
| `parsers` | array | `["pdf"]` | PDF handling. Object form: `{"type":"pdf","mode":"fast\|auto\|ocr"}` — use `ocr` for scanned case-study PDFs. |
| `removeBase64Images` | bool | `true` | Keeps markdown lean (alt text stays). Leave on. |

### 3.2 Rendering / reliability parameters

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `waitFor` | int (ms) | 0 | Extra delay for JS to load. Try 1500–3000 on JS-heavy customer carousels. |
| `timeout` | int (ms) | 60000 | 1000–300000. |
| `mobile` | bool | false | Emulate mobile; some sites serve simpler DOM. |
| `actions` | array | — | Pre-scrape browser steps: `wait`, `click`, `write`, `press`, `scroll`, `screenshot`, `scrape`, `executeJavascript`, `pdf`. Use for "load more" logo walls. `write` needs a preceding `click`; actions run sequentially; not supported on PDFs. |
| `location` | object | `{country:"US"}` | `{country, languages[]}`. Sets proxy + locale/timezone. |
| `headers` | object | — | Cookies, custom User-Agent. Useful to force a mobile UA or pass through consent cookies. |
| `proxy` | enum | `auto` | `basic` (fast, cheap), `enhanced` (advanced anti-bot, up to 5 cr), `auto` (retry with enhanced on failure; billed 5 only if enhanced succeeds). `auto` is the right default. |
| `blockAds` | bool | `true` | Ad + cookie-popup blocking. |

### 3.3 Caching parameters (your biggest cost + latency lever)

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `maxAge` | int (ms) | 172800000 (2 days) | Return cached copy if younger than this. During an eval run where freshness doesn't matter, set high (e.g. `86400000`). **Cached scrapes still cost 1 credit — caching cuts latency, not the credit.** Set `0` to force fresh. |
| `minAge` | int (ms) | — | Cache-only mode: returns cached data at least this old, else 404. Set to `1` to accept any cached data. |
| `storeInCache` | bool | `true` | If `false`, page isn't stored (data-protection). |

### 3.4 Compliance parameters (relevant — healthcare)

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `redactPII` | bool/object | `false` | Redacts PERSON/EMAIL/PHONE/LOCATION/FINANCIAL/SECRET from returned markdown. Consider enabling if evidence pages contain patient or staff PII. |
| `zeroDataRetention` | bool | `false` | Enterprise; nothing retained. |
| `lockdown` | bool | `false` | Serve from cache only; URL never logged on miss. |

---

## 4. Formats — the highest-leverage `/scrape` choice for extraction

`formats` accepts strings or objects. The ones that matter for customer discovery:

| Format | Returns | Use for your task |
|---|---|---|
| `markdown` | Clean markdown | Default; feed raw evidence to your classifier. |
| `summary` | Page summary | Quick triage of whether a page even mentions customers. |
| `json` | Schema-guided structured data (+4 credits) | **The power move.** Pass `{"type":"json","prompt":"...","schema":{...}}` to extract a structured customer list directly from a logo wall or case-study index — name, evidence quote, relationship type — in one call. |
| `highlights` | Relevant source text for a query (max 10k chars) | Pull just the customer-mention passages from a long press release. Cheap evidence isolation — directly addresses your 800-char snippet problem. |
| `question` | Natural-language answer about the page (max 10k) | "Is any organization described here as a paying customer of X? List them." Fast per-page yes/no + names. |
| `links` | All page links | Feed back into discovery; follow to sub-case-studies. |
| `screenshot` | PNG (expires 24h) | Audit/debug a logo wall the parser mangled. |
| `changeTracking` | Diff vs last scrape | Monitor a vendor customer page over time to catch newly added customers. |

**Example — structured customer extraction in one scrape call:**

```json
{
  "url": "https://vendor.com/customers",
  "onlyMainContent": false,
  "formats": [{
    "type": "json",
    "prompt": "Extract every organization named as a CUSTOMER of this product. Exclude technology integrations and co-development partners. For each, give the organization name, the exact supporting text, and your relationship classification.",
    "schema": {
      "type": "object",
      "properties": {
        "customers": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "name": {"type": "string"},
              "evidence": {"type": "string"},
              "relationship": {"type": "string", "enum": ["customer", "integration", "partner", "unclear"]}
            },
            "required": ["name", "relationship"]
          }
        }
      },
      "required": ["customers"]
    }
  }]
}
```

Note: forcing the `relationship` enum here is exactly the customer/integration/partner call you're
trying to make — done at the evidence-rich page level, which is where it belongs (per the recall
analysis in the prior review).

---

## 5. `/map` — full parameter reference (your single best Firecrawl move)

`POST /v2/map`. Discovers URLs on one site via sitemap + search index + prior-crawl cache, in
~2–3 seconds. This is how you find the vendor's own customer-proof pages without guessing URLs.

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `url` | string | — | The vendor's base domain. |
| `search` | string | — | Filter links by text match. Use `"customer"`, `"case study"`, `"success"`, `"testimonial"`, `"trusted"` to surface exactly the proof pages. |
| `sitemap` | enum | `include` | `include` (sitemap + discovery), `skip`, `only`. |
| `includeSubdomains` | bool | `true` | Keep on — case studies often live on `go.` or `resources.` subdomains. |
| `ignoreQueryParameters` | bool | `true` | Collapses `?utm=` duplicates. |
| `ignoreCache` | bool | `false` | Sitemap data cached up to 7 days; set `true` if the site just published new pages. |
| `limit` | int | up to 5000 | Max URLs. |
| `location` | object | — | Country/language — matters for international vendors. |
| `timeout` | int (ms) | none | Optional cap. |

**Caveat:** Map optimizes for speed and may miss JS-only or login-gated URLs — it's a discovery
signal, not exhaustive. Follow with `/scrape` on the hits.

---

## 6. `/extract` and `/agent` — structured extraction across pages

**`/extract`**: you supply URLs, it LLM-extracts to your schema.

| Parameter | Notes |
|---|---|
| `urls` | Array of pages to extract from (supports wildcards like `vendor.com/customers/*`). |
| `prompt` | Extraction instruction. |
| `schema` | JSON Schema for the output shape. |
| `allowExternalLinks` | Let it follow off-site links. |
| `enableWebSearch` | Let it pull extra web context beyond the given URLs — useful to confirm a borderline customer. |
| `includeSubdomains` | Include subdomains. |

**`/agent`** (`POST /v2/agent`) is the autonomous evolution — you don't need URLs upfront; it
searches, follows links, and extracts on its own. Runs async (returns a job id; poll
`/v2/agent/{id}` or use the SDK's `agent()` auto-poll).

| Parameter | Notes |
|---|---|
| `prompt` | Up to 10,000 chars — "Find paying customers of {product} by {vendor}…". |
| `urls` | Optional URLs to constrain to. |
| `onlyProvidedUrls` | If `true`, visit only those URLs. |
| `schema` | Output structure. |
| `maxCredits` | Spend cap — your safety valve. |

**How to use `/agent` for you:** it's a strong baseline to benchmark against your own pipeline —
one call per product, compare its F1 to your GPT-5+Exa system. Treat it as a yardstick and a
fallback, not the production path (less control over recall/precision knobs).

---

## 7. `/crawl` — when you want a whole section

`POST /v2/crawl`. Async (job id → poll `get_crawl_status`, WebSocket `watcher()`, or webhook).
Cost: 1 credit/page + format extras.

Key parameters: `limit`, `includePaths`/`excludePaths` (regex on pathname), `maxDiscoveryDepth`,
`crawlEntireDomain`, `allowExternalLinks`, `allowSubdomains`, `sitemap`, and `scrapeOptions` (all
§3 options apply per page).

**For you:** crawl a scoped subtree, e.g. `includePaths: ["/customers", "/case-studies",
"/resources"]` with a modest `limit`, rather than the whole domain — keeps credits bounded and
precision high.

---

## 8. Recommended per-product configuration

A concrete, cost-aware recipe:

1. **Discover proof pages** — `/map` with `search:"customer case study success testimonial
   trusted"`, `includeSubdomains:true`, `limit:200`.
2. **Deep-read them** — `/scrape` each hit with `onlyMainContent:false`,
   `formats:[{json+schema as in §4}, "highlights"(query:"customer of {product}")]`,
   `proxy:"auto"`, `maxAge:86400000`, `parsers:[{type:"pdf",mode:"auto"}]`.
3. **Press-wire pass** — `/search` `sources:["news","web"]`,
   `includeDomains:["prnewswire.com","businesswire.com"]`, `tbs:"qdr:y"`,
   `scrapeOptions:{formats:["markdown"],onlyMainContent:true}`, `ignoreInvalidURLs:true`.
4. **Review sites** — `/scrape` G2/Capterra pages with `onlyMainContent:false` + `actions`
   (scroll/"load more") or Interact for filtered directories.
5. **Bounded fallback** — if a vendor has a big `/customers/*` tree, `/crawl` that subtree with
   `includePaths` and a `limit`.
6. **Classify downstream** in your own pipeline (keep the customer/integration/partner decision
   where the full evidence is).

**Cost discipline:** reserve the +4-credit `json` format for pages likely to contain a customer
list (logo walls, case-study indexes); use plain `markdown` + `highlights` elsewhere. Set `maxAge`
generously during eval runs so repeated experiments hit cache (latency win; note credits are still
charged per scrape).

---

## 9. Cost & rate-limit model (quick reference)

| Operation | Credit cost |
|---|---|
| Scrape (base) | 1 |
| `json`/extract format | +4 per page |
| Enhanced proxy | up to +4 (only if enhanced backend runs) |
| PDF parsing | 1 per PDF page |
| Threat-protection URL scan | +2 per URL |
| Lockdown | 5 on cache hit, 1 on miss |
| Search | 2 per 10 results (+ scrape cost if `scrapeOptions`) |
| Crawl | 1 per page (+ format extras) |
| Map | light; discovery-priced |
| Search ZDR / anon | 10 / 2 per 10 results |

Search rate limits by plan (req/min): Free 5, Hobby 50, Standard 250, Growth 2,500. Standard plan
is roughly $83–99/mo for ~100k credits (confirm on the live pricing page — plans change).

---

## 10. Access methods & ops notes

- **SDKs:** Python (`firecrawl-py`), Node (`firecrawl-js`), plus Go and Rust. SDKs auto-poll
  async jobs.
- **MCP server:** `claude mcp add --transport http firecrawl https://mcp.firecrawl.dev/fc-YOUR-KEY/v2/mcp`
  — exposes `firecrawl_search`, `firecrawl_scrape`, `firecrawl_map`, `firecrawl_extract`,
  `firecrawl_agent`, `firecrawl_parse` as tools.
- **CLI:** `npm i -g firecrawl-cli`; `firecrawl search/scrape/map/crawl …`
- **Self-hosting:** open source (AGPL-3.0); heavier stack (Redis + Playwright + multiple
  containers) — worth it only for data-sovereignty needs, which given healthcare you may actually
  have.

---

## 11. Pitfalls specific to your task

- **`onlyMainContent:true` deletes logo walls.** The most important single setting for you. Flip
  it to `false` on customer/proof pages.
- **Bare `/search` = snippets.** Always add `scrapeOptions` when you need page bodies.
- **`includeTags`/`excludeTags` target the original DOM**, not the cleaned output — write
  selectors against the raw page.
- **Actions don't work on PDFs**, and `write` needs a preceding `click`. For scanned case-study
  PDFs use `parsers` with `mode:"ocr"`, not `actions`.
- **Caching saves time, not credits.** Don't expect `maxAge` to cut your bill — it cuts latency.
  Budget credits on scrape count.
- **Map isn't exhaustive.** Pages behind JS/login/forms may be missed — combine with `/search
  site:vendor.com` and Interact.
- **`json` format is +4 credits every time.** Gate it to pages likely to hold a customer list;
  use `highlights`/`question` (cheaper) for isolated evidence elsewhere.
- **Healthcare PII.** If evidence pages carry patient/staff PII, `redactPII` (and possibly
  ZDR/lockdown) keep it out of your store — a compliance point worth raising before scale-up.
