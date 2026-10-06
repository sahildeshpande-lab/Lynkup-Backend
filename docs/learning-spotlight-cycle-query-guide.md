# Learning Spotlight — Cycle & Query Summary

Personalized daily research from Semantic Scholar. One category per calendar day on a **5-day global cycle**.

---

## 1) Cycle (5 days)

| Day | Cycle (`spotlight_type`) | Product intent |
|-----|--------------------------|----------------|
| 1 | `leading_thinker` | Research associated with high-impact authors in the user’s field |
| 2 | `country_perspective` | Field research biased toward the user’s country |
| 3 | `influential_research` | Highly cited papers in the user’s field (≥ 10 citations) |
| 4 | `latest_research` | Recently published papers (prior calendar year onward) |
| 5 | `beyond_your_field` | Research outside major/minor, driven by interests |

---

## 2) How we send the Semantic Scholar query

**Field priority (most cycles):** `major` → `minor` → `interests` → engagement/content/hashtags.

**Query framing:**
1. Each topic becomes a concept group; concepts inside a group are AND-joined with `+`.
2. Multi-word phrases are quoted (`"Early Childhood Education"`); single tokens are not (`Education`).
3. Groups are OR-joined with `|`:
   ```text
   (Education)|(Leadership)|("Early Childhood Education")|(motivation)
   ```
4. Request: `search_papers_v2` with that `query`, default `limit=50`, `fieldsOfStudy` not sent.
5. Papers must have abstract + usable open-access PDF. Fallback stages simplify groups if results are too few (never broaden into unigram OR soup).

**Per-cycle differences:**

| Cycle | What changes in the request |
|-------|-----------------------------|
| Leading Thinker | Same field query; then `/author/batch` to score high-impact authors |
| Country Perspective | Only major → minor → interests; each concept AND-paired with country: `("Concept"+Country)\|…` |
| Influential Research | Same field query; **no year**; keep papers with `citation_count ≥ 10` |
| Latest Research | Same field query + `year="{current_year-1}-"` (e.g. in 2026 → `2025-`) |
| Beyond Your Field | **Drops major & minor**; query from interests only (interdisciplinary fallback if empty) |

---

## 3) Alex examples — exact queries

**User:** `alex.osei-kojo@ucdenver.edu`

Full-field cycles send all four terms. Beyond Your Field drops Education/Leadership (major/minor) and keeps interests only.

| # | Cycle | Exact query sent |
|---|--------|------------------|
| 1 | `latest_research` | `(Education)\|(Leadership)\|("Early Childhood Education")\|(motivation)` |
| 2 | `influential_research` | `(Education)\|(Leadership)\|("Early Childhood Education")\|(motivation)` |
| 3 | `beyond_your_field` | `("Early Childhood Education")\|(motivation)` |
| 4 | `latest_research` | `(Education)\|(Leadership)\|("Early Childhood Education")\|(motivation)` |
| 5 | `beyond_your_field` | `("Early Childhood Education")\|(motivation)` |

**Country Perspective shape (same profile, if country = Ghana):**

```text
(Education+Ghana)|(Leadership+Ghana)|("Early Childhood Education"+Ghana)|(motivation+Ghana)
```

**Latest Research** also sends `year=2025-` (when generated in 2026). **Influential** uses the same query string with no year filter, then filters to ≥10 citations.
