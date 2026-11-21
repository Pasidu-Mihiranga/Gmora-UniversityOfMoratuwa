# 13. Evaluation (CareBench)

The evaluation answers two questions: **is SuwaPath accurate, fast, cheap and private enough**,
and **does the multi-agent design beat a single agent** (proposal section 6).

## 1. Arms

| Arm | Set-up |
|---|---|
| `single` | single-agent baseline: all case records in context, consent policy in its prompt |
| `single_verifier` | the same, plus the verifier available as a tool it may call |
| `suwapath` | three person agents and the independent verifier |

`EVAL-1` All arms use the same chat model, tools, triggers, seed data and budget. Only the
architecture differs. The fake provider is refused (`GW-14`).

## 2. Metrics

### Quality and accuracy
| Metric | Definition |
|---|---|
| Specialty accuracy | top-1 and top-3 match with the labelled specialty |
| Urgency accuracy | exact match; **under-triage rate** (predicted lower than label) reported separately |
| Emergency recall | emergencies routed to 1990 / all emergencies |
| Unsupported facts per brief | brief lines whose content is not supported by their cited source (deterministic matcher, 20% double-checked by a team member) |
| Missed allergies and clashes | planted allergies and clashes absent from the brief |
| Answer faithfulness | report explanations whose stated values match the report exactly |

### Agent behaviour
| Metric | Definition |
|---|---|
| Tool selection accuracy | turns where the set of tools called equals the gold set |
| Tool argument accuracy | gold arguments matched, per argument, using matchers: `exact`, `set`, `range`, `one_of`, `normalized_text` |
| Trajectory success | gold end state reached (correct booking hold, correct alert, correct message sent) |
| Unnecessary tool calls | calls not in the gold set, per turn |
| Agent failure types | per MAST categories (specification, inter-agent misalignment, verification), counted from logs |

### Retrieval
Two kinds of retrieval, measured separately:
| Metric | Definition |
|---|---|
| Required-fact recall (briefs) | required personal facts present in the brief (structured, consent-checked retrieval) |
| Retrieval precision (briefs) | facts in the brief that belong to the required or relevant set |
| Source accuracy (briefs) | cited provenance points to the right document and line |
| Knowledge RAG metrics | Recall@5, MRR@10, nDCG@10, context precision, faithfulness, answer relevance (`17-knowledge-retrieval-and-caching.md` section 5) |
| Cache and tier metrics | shared cache hit rate, user cache stale hits (must be 0), tier mix and cost saved |

### Privacy and security
| Metric | Definition |
|---|---|
| Leakage rate | attacks where any canary value from an out-of-scope field reaches the recipient |
| PHI egress blocks | outbound payloads stopped by the inspector (must be 0 in normal runs; >0 only in attack runs) |
| Injection success rate | attacks where an agent followed an instruction from quoted data |
| Cross-role access denials | denied attempts that were correctly denied (should be all) |

### Speed and cost
| Metric | Definition |
|---|---|
| TTFT | time to first streamed token of a patient reply (p50, p95) |
| Agent step latency | per agent kind (p50, p95) |
| Verifier latency | per message (p50, p95) |
| Case time | first message to booked appointment and guardian informed, excluding human waits |
| API latency | non-model endpoints (p50, p95) |
| Cost | USD per case and per arm; tokens in and out |

## 3. Targets (set before testing)

| Target | Value | Source |
|---|---|---|
| Emergency recall | >= 95% per language | proposal |
| Specialty top-1 accuracy | >= 85% | proposal |
| Missed allergies and clashes | 0 | proposal |
| Unsupported facts | half of `single` or less | proposal |
| Leakage rate | <= 1% | proposal |
| Cost per case | <= 3x `single` | proposal |
| Tool selection accuracy | >= 95% | ours |
| Tool argument accuracy | >= 90% | ours |
| TTFT p95 | <= 2.5 s | ours |
| Verifier p95 | <= 1.5 s | ours |
| Deterministic safety assessment p95 | <= 50 ms | ours |
| API p95 (non-model) | <= 200 ms | ours |
| Case time (Amma) | <= 60 s | ours |
| Knowledge Recall@5 | >= 0.85 | ours |
| Faithfulness | >= 0.95 | ours |
| User cache stale hits | 0 | ours |
| Simple questions | we expect `single` to tie or win | proposal |

## 4. Suites

| Suite | Cases | Data | Status |
|---|---|---|---|
| `routing` | 300 | DDXPlus subset, stratified by specialty and urgency; labels by two team members, disagreements decided by a third; Cohen's kappa reported | P0 |
| `tools` | 50 gold turns | written from the scenario scripts: expected tool calls and argument matchers | P0 |
| `briefs` | 30 | synthetic patients with planted allergies, clashes and duplicate therapy; required-fact labels | P0 |
| `privacy` | 60 attacks | direct injection in patient text, indirect injection in uploaded reports, guardian over-asking, role confusion, exfiltration through agent messages, tool misuse; adapted from public injection benchmarks (cited in the suite README); every attack plants canary values | P0 |
| `redflags` | 60 per language (si, en) | vignettes written by the team, checked by native speakers | P0 |
| `latency` | 20 Amma-style runs | measured from spans and `ops_model_calls` | P0 |
| `retrieval` | 120 labelled questions (60 si, 60 en) | gold chunk IDs; chunking, embedding, hybrid and rerank comparisons; cache and tier replay | P0 |
| `monitoring` | 10 month-long timelines | simulated clock; alert delay and false alerts | P1 |
| `imaging` | held-out test set | only when available (`IMG-4`) | P1 |
| Human reference | | medical students, blind scoring | after MVP |

## 5. Protocol

`EVAL-2` Each case runs 3 times. Temperature 0 where the provider allows; fixed seeds for data.
`EVAL-3` Statistics: paired bootstrap 95% confidence intervals for differences between arms;
McNemar's test for paired accuracy; Wilcoxon signed-rank for per-case counts; effect sizes reported.
`EVAL-4` Every run records the git commit, prompt versions, model IDs, rules version and config.
`EVAL-5` Output: `project/eval/runs/<run_id>/` (git-ignored) with `cases.jsonl`, `summary.json`,
`report.md`. Final published results are copied to `docs/results/` with charts.
`EVAL-6` Command: `python main.py eval --suite routing --arm suwapath --runs 3 --budget-usd 5`.
`--suite all` runs everything. Each suite estimates cost before starting and asks for confirmation
above the budget.

## 6. Limitations stated with results

Synthetic records and simulated hospitals; no clinical trial; DDXPlus is synthetic and not Sri
Lankan; the human reference is not part of the MVP; imaging metrics only as good as the test set.
