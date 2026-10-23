> Historical research record. Refer to `docs/reproduction.md` for the public setup, available artifacts, and limits. Original run links and private dependencies are not required. Claims below are historical observations, not current performance guarantees.

# Literature & Tool Review: LLM-Based Customer Discovery via Web Search

Prepared for a healthcare-IT customer-discovery agent. Scope: agentic web-search extraction,
multi-agent research architectures, search/crawl APIs, recall-vs-precision prompting, and
evaluation. Current baseline to beat: Claude Opus 4.8 + web search, F1 = 39.77%.

---

## 0. The one-paragraph diagnosis (read this first)

Your numbers tell a clear story that reframes the whole effort: your problem is recall, not
precision, and your most sophisticated architecture made recall worse. Every GPT-5 + Exa variant
lands at 47–49% precision but only 20–27% recall. The Claude baseline wins almost entirely
because its recall is higher (37.9%). The scatter-gather + synthesizer v4 result is the tell:
precision hit an all-time high (49%) exactly as recall collapsed to 20.8%. That is the textbook
signature of an over-filtering LLM-as-judge step — the synthesizer re-classifies candidates with
less evidence context than the sub-agents that found them, and conservatively drops the
borderline-but-valid ones. So the single highest-leverage change is not a new architecture; it is
to stop the synthesizer from acting as a judge, and to add recall-only passes that can add
customers but never remove them. Everything below is organized around that.

---

## 1. Most relevant papers

Ordered by how directly they bear on your task.

### 1.1 ReAct: Synergizing Reasoning and Acting in Language Models
Yao et al., ICLR 2023 — arXiv:2210.03629

The formal basis of the "think → search → observe" loop you already run via LangGraph's
`create_react_agent`. Interleaving reasoning traces with tool actions lets the model plan the next
query from what it just saw, and reduces the hallucination that pure chain-of-thought suffers when
ungrounded. **Relevance:** confirms your single-agent design is sound; the ceiling you're hitting
is not the ReAct pattern itself but context accumulation and the final serialization step.

### 1.2 Reflexion: Language Agents with Verbal Reinforcement Learning
Shinn et al., NeurIPS 2023 — arXiv:2303.11366

Splits an agent into Actor / Evaluator / Self-Reflection roles, where the reflection is verbal
feedback stored in episodic memory and fed back on the next attempt — no weight updates.
**Relevance:** this is the template for a recall-oriented critique agent. A "what did we miss?"
pass that reflects on the current customer list and re-searches for gaps is a direct Reflexion
application, and it is monotonic for recall if you forbid it from deleting entries.

### 1.3 How we built our multi-agent research system
Anthropic Engineering, 2025 — anthropic.com/engineering/multi-agent-research-system

The closest public analogue to your scatter-gather system, and the single most useful
practitioner reference here. Key points that map onto your setup:

- **Orchestrator-worker pattern:** a lead agent plans and spawns 3–5 subagents in parallel, each
  with its own context window, then synthesizes. A separate citation agent runs as its own pass.
- Reported ~90% improvement over single-agent Opus 4 on their internal research eval, at roughly
  15× the token cost — multi-agent only pays off for high-value, breadth-first tasks (customer
  discovery qualifies).
- The subagent "contract" they converged on has four parts: objective, output format, tool/source
  guidance, and task boundaries. Missing any one causes drift.
- Failure mode they explicitly call out: agents spawning excess subagents and doing redundant
  work. Their fix was prompt engineering and effort-scaling, not more agents.

**Relevance:** validates parallel fresh-context subagents as the fix for your overflow problem,
and suggests two upgrades — a dynamic orchestrator that picks sources per product rather than a
fixed 4-way split, and treating synthesis/citation as separate concerns.

### 1.4 Self-Consistency Improves Chain of Thought Reasoning in Language Models
Wang et al., 2022 — arXiv:2203.11171

Sample multiple reasoning paths, then aggregate by majority vote. **Relevance — with a critical
caveat:** majority vote is designed for single-answer tasks. Your output is a set. Naïve majority
voting would intersect runs and destroy recall. The correct adaptation for a set-valued recall
problem is to take the **union** across N runs (boosts recall), then control precision downstream
with an evidence filter — or use a K-of-N appearance threshold as a tunable precision knob. This
inversion is easy to get wrong and matters a lot for you.

### 1.5 LLMs-as-Judges: A Comprehensive Survey
2024 — arXiv:2412.05579 (see also the ScienceDirect "Survey on LLM-as-a-judge", 2026)

Catalogs the systematic biases of LLM evaluators: position bias, verbosity bias, self-enhancement
bias, and general instability under prompt/format perturbation. **Relevance:** this is the
literature that explains your synthesizer's recall collapse. A strict re-classification pass is an
LLM judge, and it inherits all of these failure modes — plus it operates on compressed sub-agent
summaries rather than the raw evidence, so it is judging with less information than the finder
had. The paper's mitigation theme (aggregate multiple judges, don't trust a single conservative
pass) argues for moving classification into the evidence-rich sub-agents.

### 1.6 "Let Me Speak Freely?" — the format tax on structured output
Tam et al., 2024 (widely replicated; see arXiv:2505.04016 "SLOT" and follow-ups)

Documents that forcing LLMs into rigid JSON/XML degrades reasoning-heavy task accuracy by roughly
10–15%, with stricter constraints causing larger drops, and that field ordering matters —
reasoning fields must precede answer fields because models generate left-to-right. **Relevance:**
two hits for you. First, your GPT-5 truncation-mid-JSON failure (no customers returned for several evaluated products) is the acute version of this. Second, even where JSON
completes, format pressure is quietly taxing the per-candidate reasoning that governs the
customer/integration/partner call. Fix: reason and enumerate candidates in free text first, then
serialize in a cheap separate call.

### 1.7 BrowseComp: A Simple Yet Challenging Benchmark for Browsing Agents
Wei et al. (OpenAI), 2025 — arXiv:2504.12516

1,266 questions requiring persistent, multi-hop browsing for "hard-to-find but easy-to-verify"
facts. Reported finding: naïve browsing tools score near-zero while systems trained/tuned for
persistence and backtracking score far higher. **Relevance:** your task is BrowseComp-like per
customer (indirect evidence scattered across press releases, case studies, logo walls). The lesson
is that persistence and backtracking — not raw search volume — separate good browsing agents from
bad ones. An agent that gives up after one thin snippet is your recall leak.

### 1.8 Search-R1: Training LLMs to Reason and Leverage Search with RL
Jin et al., 2025 — arXiv:2503.09516 (see also R1-Searcher, Search-o1, ZeroSearch)

RL-trains a model to emit multiple search queries during reasoning, using retrieved-token masking
for stable training and outcome-based rewards; reports +41% over RAG baselines on QA. **Relevance:**
this is the frontier for your problem type, but it needs training infrastructure and reward data
you don't have, and it conflicts with your "no dozens of experiments" and cost constraints. Flag
it as the horizon, not the next sprint. The cheaper, promptable lesson you can steal today: reward
persistence and multi-query breadth explicitly in the prompt.

### 1.9 Entity matching / resolution with LLMs
Peeters & Bizer 2023 (arXiv:2310.11244); Wang et al. 2025 "Match, Compare, or Select?" (arXiv:2405.16884)

The blocking-and-matching pipeline is still the mainstream: cheap blocking to generate candidate
pairs, then LLM matching to judge them. LLMs do zero/few-shot entity matching competitively.
**Relevance:** your "Mayo Clinic Rochester = Mayo Clinic" dedup. Do it as an explicit blocking +
pairwise-match step, not as a side-effect of the synthesizer prompt — but tune it carefully,
because over-canonicalization can merge two genuinely distinct health-system entities that are
both real customers (a recall risk hiding inside a dedup step).

### 1.10 WebGPT (foundational context)
Nakano et al. (OpenAI), 2021 — arXiv:2112.09332

The origin point: GPT-3 fine-tuned to operate a text browser and answer long-form questions with
citations, trained via imitation plus human feedback. Historically important for establishing
browse-with-citations; less directly actionable than the above, included so the lineage is
complete (WebGPT → ReAct → Search-R1).

---

## 2. Search / crawl API comparison

Your bottleneck is recall and evidence depth, so read this table through two lenses: does it help
find more candidates and does it give enough evidence per page without blowing the context budget.

| API | Type | Strengths for this task | Weaknesses / risks | Verdict for you |
|---|---|---|---|---|
| **Exa** (current) | Neural/semantic search + contents | `findSimilar` expands from a confirmed customer page to sibling pages (recall superpower); `highlights` returns only the most relevant excerpts (cuts the token bloat that caused your truncation) | Semantic search can drift off-keyword; cloud-only | Keep, but use `findSimilar` + `highlights` — you may not be exploiting either |
| **Firecrawl** | Scrape/crawl → clean markdown | Full-page markdown with JS rendering; JSON extraction with schema; good for reading a "trusted by" logo wall or a case-study PDF in full | Per-page credit cost adds up; you reported thin results — likely a config issue (using search mode, not deep scrape) | Add as a deep-read tool for the 2–3 high-value pages per product, not as primary search |
| **Tavily** | Search-first for agents | LangChain-native, clean model-ready output, `/research` sub-query endpoint | Acquired by Nebius (Feb 2026) → roadmap uncertainty; snippet-oriented | Viable Exa alternative, but Exa's `findSimilar` matters more for recall |
| **Perplexity Sonar** | Search + LLM synthesis in one call | Cited answers out of the box | You pay tokens and search; little control over sources/synthesis; expensive at agent scale | Skip — you already have a reasoning model; you want raw evidence, not pre-synthesis |
| **Parallel AI** | Provenance-first agent search | Every result carries evidence/provenance; strong on hard retrieval benchmarks | Newer vendor; less ecosystem | Worth a bake-off — provenance-per-result fits your "evidence URL + snippet" output schema |
| **Bing Web Search API** | SERP | — | Retired Aug 2025 | Do not build on it |
| **SerpAPI / Serper** | Raw SERP metadata | Cheap Google SERPs; good for exhaustive `site:` sweeps of prnewswire/businesswire | SerpAPI hit with a Google lawsuit (Dec 2025) → provider risk; Serper is the cheaper, lower-risk sibling | Useful as a cheap breadth layer under a scraper |
| **Brave Search API** | Independent index | Own 30B-page index (not Bing/Google dependent); cheap; privacy/compliance friendly | Snippets need a scraper for depth | Good hedge as a second, independent search provider |
| **Jina Reader** | URL → markdown | Near-free URL-to-markdown (`r.jina.ai/`), token-billed | Not a search engine; formatting only | Cheapest way to deep-read a known high-value URL |
| **Diffbot** | Structured KG / entity extraction | Turns pages into typed org entities; a Knowledge Graph of companies | Heavyweight; overkill unless you need firmographic enrichment | Optional, for org disambiguation / enrichment later |
| **Common Crawl** | Bulk web corpus | Free at scale | Not real-time — misses fresh press releases; heavy infra | Skip for this use case |

**Recommended stack shape:** a breadth layer (Exa neural + one independent engine like Brave/Serper)
→ a deep-read layer (Firecrawl or Jina) on the handful of evidence-dense pages → Exa `findSimilar`
seeded from every confirmed customer to chase recall. Search snippets at 800 chars are simply too
thin for logo walls and case studies; the missing deep-read step is a plausible chunk of your
recall gap.

---

## 3. Architectural recommendations (ranked)

### Tier 1 — do these first (attack the recall collapse)

**R1. Demote the synthesizer from judge to merger.** Move all CUSTOMER / INTEGRATION / PARTNER
classification into the sub-agents, where the full evidence context lives. The final pass should
do only three things: (a) union all sub-agent outputs, (b) entity-resolve/dedup, (c) format. No
re-classification, no "strict final pass." Your v5 "trust sub-agents more" change is the right
direction — this is its logical endpoint. Expected effect: recall recovers toward and past baseline
while precision stays near the sub-agents' ~48%.

**R2. Add a recall-only gap-filling / critique agent (Reflexion-style).** After merge, run one
pass whose only job is: "Here is the current list for product X. Find paying customers we missed.
You may add; you may not remove." Because it is additive-only, it cannot hurt recall and can
meaningfully raise it. Seed it with the sources the other sub-agents under-covered.

**R3. Self-consistency by UNION, not majority vote.** Run the sub-agent stage N=2–3 times
(temperature > 0) and union the discovered customers. Control precision with an evidence-quality
filter or a K-of-N appearance threshold — not by intersecting runs. This trades your scarce eval
budget for recall you're currently leaving on the table. (See §1.4 for why the vanilla technique
must be inverted.)

### Tier 2 — solid, moderate impact

**R4. Decouple reasoning from JSON serialization.** Have each agent reason and enumerate
candidates in free text, then a cheap final call serializes to your JSON schema. Directly addresses
the format tax (§1.6) and eliminates the truncation-mid-JSON failure at the source rather than
only via fresh contexts. Keep reasoning fields before answer fields in any schema you do enforce.

**R5. Per-candidate evidence classification with explicit taxonomy + CoT.** The
customer/integration/partner distinction is where precision is legitimately won — but win it with
evidence-grounded per-candidate chain-of-thought inside the sub-agent, not with a conservative
global filter. Give a crisp decision rule and 2–3 worked examples of each class (especially a
tech-integrator false positive and a peer-partner false positive, since those are your named
failure modes).

**R6. Dynamic orchestrator + a dedicated "customer-proof pages" sub-agent.** Replace the fixed
4-way split with an orchestrator that first inspects the product/vendor and decides which sources
exist (not every product has a G2 page). Add a sub-agent specialized on vendor
customer/case-study/"trusted by" pages — in healthcare IT these logo walls and case studies are
the densest, highest-precision evidence. Give each sub-agent Anthropic's four-part contract
(objective, format, source guidance, boundaries).

### Tier 3 — worth trying, lower confidence

**R7.** Explicit entity resolution (blocking + pairwise LLM match) for dedup, tuned conservatively
so you don't merge distinct health-system entities (§1.9).

**R8.** Recall-oriented query expansion: hybrid neural+keyword, aggressive `site:` sweeps of the
wire services, and `findSimilar` seeded from confirmed customers.

**R9.** RL-trained search (Search-R1 family): the frontier, but out of scope under your
cost/latency constraints. Revisit only if promptable approaches plateau.

---

## 4. Techniques ranked by expected F1 impact

| Rank | Technique | Why | Effort | Risk |
|---|---|---|---|---|
| 1 | Synthesizer → merger, classify in sub-agents (R1) | Directly reverses the observed recall collapse | Low (prompt/flow change) | Low |
| 2 | Recall-only critique/gap agent (R2) | Monotonic recall gain, additive-only | Low–med | Low |
| 3 | Union-based self-consistency (R3) | Set-valued recall lever the baseline lacks | Med (N× cost) | Med (cost) |
| 4 | Add deep-read scraper + Exa `findSimilar`/`highlights` (§2) | More & richer evidence per page; chases sibling customers | Med | Low |
| 5 | Decouple reasoning from JSON (R4) | Removes format tax + truncation failure | Low | Low |
| 6 | Per-candidate evidence CoT with taxonomy (R5) | Precision without conservative over-filtering | Low | Low |
| 7 | Dynamic orchestrator + customer-page sub-agent (R6) | Better source coverage per product | Med | Low |
| 8 | Explicit entity resolution (R7) | Cleaner dedup | Med | Med (over-merge) |

The first two are almost free and target the exact failure your data shows. Do them before
spending eval budget on anything else.

---

## 5. Evaluation notes

**Is F1 even your target?** You state both errors are costly, but your data is lopsided toward
precision (≈2:1 P:R). Equal-weighted F1 rewards that imbalance. During development, consider
optimizing F-beta with beta > 1 to push the recall frontier you're actually stuck on, then dial
back to F1 (or your real cost ratio) for final selection. Make the metric reflect the true
business cost of a missed customer vs. a polluting false positive.

**Per-product variance is huge.** Four products returning 0 customers (the truncation failures)
drag averages catastrophically. Report per-product F1 and treat "returned nothing" as a distinct,
separately-tracked failure class — it's an engineering bug, not a modeling limitation, and
averaging hides it.

**Benchmarks to borrow methodology from:** BrowseComp (§1.7) for the "hard-to-find,
easy-to-verify" evaluation philosophy; its inverted-question construction (start from a verified
fact, confirm it's not on page one of search) is a good template for building harder ground-truth
cases. Note BrowseComp is single-answer, not set-valued, so borrow the methodology, not the
metric.

**Known F1 failure modes here:** ground-truth incompleteness (a "false positive" may be a real
customer your annotators missed — corrosive when recall is the goal), and entity-granularity
mismatches (parent vs. subsidiary counted inconsistently). Audit a sample of your "false
positives" by hand; some are likely true customers, which means your real precision is higher and
your ground truth needs patching.

---

## 6. Things you didn't ask about that matter

**Your architecture got more sophisticated and less effective — that's the headline.** The
multi-agent system solved overflow perfectly and then introduced a worse bottleneck (a filtering
synthesizer). More structure added a place to lose customers. The fix is subtraction, not addition.

**The baseline probably wins for a boring reason: fewer lossy hops.** Claude + web search is one
model, one context, one pass — no synthesizer to discard borderline cases. Before out-engineering
it, match its simplicity on the recall path: your sub-agents likely already beat it on precision;
you just need to stop throwing their finds away.

**`findSimilar` is an unused recall multiplier.** Every confirmed customer's press release or
case study is a seed; `findSimilar` surfaces the vendor's other customer pages that keyword search
misses. This is arguably the highest-recall-per-dollar move available in your current stack.

**The truncation failures were a format-tax symptom, not just a context-size problem.** Fresh
contexts hid it; decoupling reasoning from JSON serialization removes it. Watch for it
re-appearing on any product with evidence concentrated on a few very long pages.

**Watch dedup as a hidden recall killer.** Aggressive canonicalization ("Mayo Clinic Rochester"
→ "Mayo Clinic") can silently merge two legitimately distinct customer entities within a health
system. Instrument dedup with before/after counts so a precision fix doesn't quietly cost you
recall.

---

## Key references

- Yao et al. 2023, ReAct — arXiv:2210.03629
- Shinn et al. 2023, Reflexion — arXiv:2303.11366
- Anthropic 2025, Multi-agent research system — anthropic.com/engineering/multi-agent-research-system
- Wang et al. 2022, Self-Consistency — arXiv:2203.11171
- LLMs-as-Judges survey 2024 — arXiv:2412.05579
- Tam et al. 2024, format-restriction / "format tax" (and SLOT, arXiv:2505.04016)
- Wei et al. 2025, BrowseComp — arXiv:2504.12516
- Jin et al. 2025, Search-R1 — arXiv:2503.09516
- Peeters & Bizer 2023, LLM entity matching — arXiv:2310.11244; Wang et al. 2025 — arXiv:2405.16884
- Nakano et al. 2021, WebGPT — arXiv:2112.09332
