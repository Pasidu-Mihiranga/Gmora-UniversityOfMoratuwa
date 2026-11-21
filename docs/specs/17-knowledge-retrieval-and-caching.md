# 17. Knowledge retrieval (RAG), cost tiers and caching

Two goals: answers grounded in trusted health knowledge with citations, and **no model call when a
cheaper path gives the same answer**.

## 1. Cheapest capable path (cost and latency tiers)

`KNW-1` Every patient, guardian or doctor request is resolved by the cheapest tier that can answer it
safely. Tiers are tried in order; a tier either answers, or passes the request on.

| Tier | What | Typical latency | Cost | Examples |
|---|---|---|---|---|
| T0 Rules | deterministic code: red flags, urgency floor, clash list, adherence, routing weights, templates | < 50 ms | none | "I have chest pain" (emergency lane), "did I take my evening tablets?" |
| T1 Cache | shared knowledge cache or user-scoped cache (section 4) | < 100 ms | none | "What is HbA1c?" asked by anyone before |
| T2 Classify | intent and slot classification by a decision model, then direct tool call without a chat model | < 1 s | input tokens only | "Book a heart doctor in Kandy next week" -> `navigation.find_doctors` with extracted slots |
| T3 Generate | chat model with retrieved knowledge and consented personal facts | 1 to 3 s (streamed) | full | "Explain my report", open conversation, briefs, replies |

`KNW-2` **Safety always runs first and is never cached.** Every patient message goes through T0
red-flag assessment before any tier answers it, even on a cache hit.

`KNW-3` **Intent classification** (T2) runs behind an `IntentClassifier` port. The default adapter
uses Jev (`DecisionModel`, one `choice` question over the intent catalog); the fallback adapter, used
when no decision provider is configured, is a keyword classifier. Both return a typed result:

```python
class IntentResult(BaseModel):
    intent: Intent                    # enum of the catalog below, or "unsupported"
    confidence: float                 # top probability
    slots: IntentSlots                # typed parameters for that intent (doctor name, specialty,
                                      # hospital, date window, time of day, language, ...)
    missing_slots: list[str]          # required parameters not found
    classifier: Literal["jev", "keywords"]
```

`KNW-3a` If `confidence < 0.75`, the intent is `unsupported`, or required slots are missing and cannot
be asked for in one short question, the request falls back to T3. **The classifier never bypasses T0
safety or consent:** red-flag assessment runs before classification, and every tool call it triggers
goes through the normal principal, access policy and audit path.

| Intent | Resolved by |
|---|---|
| `emergency_or_symptom` | T0 safety, then companion intake (T3) |
| `general_health_question` | T1 shared cache, else T3 with knowledge RAG (then cached) |
| `my_record_question` | T1 user cache, else T3 with consented personal facts + knowledge RAG |
| `report_explanation` | T1 user cache, else T3 (report observations + knowledge RAG) |
| `book_or_find_doctor` | T2 slot extraction -> navigation and booking tools directly |
| `medicine_taken_or_schedule` | T0 care tools directly |
| `sharing_or_settings` | T2 -> consent rule parse flow |
| `smalltalk_or_other` | T3 short reply, low token budget |

`KNW-4` Tool calls that T2 makes directly are the same tools agents use, with the same principal,
allowlist and audit (`AGT-41`). T2 never answers clinical questions by itself.

`KNW-5` **Patient documents are not embedded in the MVP.** Report explanations combine the patient's
consent-checked structured values (with page and line provenance) with chunks from the shared corpus.
Embedding unparseable patient documents may be reconsidered later with its own ADR.

## 2. Knowledge corpus (non-PHI)

`KNW-10` The corpus contains **only public, non-personal health knowledge**. Personal data never
enters the corpus or its embeddings.

`KNW-11` MVP content, in English and Sinhala where available:
- lab test explainers (the 30 tests of `REP-2`): what the test measures, what high or low can mean in
  general terms, what to ask the doctor;
- chronic condition self-care for diabetes, hypertension, osteoarthritis, kidney disease, COPD;
- medicine information in plain language (what it is for, common side effects to report), never
  dosing instructions;
- Sri Lankan care navigation: 1990, specialties and what they treat, clinic types, how channelling
  works;
- elder-care guidance: falls, dizziness, hydration, heat, medication routines.

`KNW-12` Every document records `source_url`, `publisher`, `licence`, `retrieved_at`, `language`.
Only sources whose licence allows reuse are ingested (for example public domain or open government
licences, or documents written by the team). The corpus README lists every source.

`KNW-13` Retrieved knowledge is quoted to the model inside a data block with its citation ID, and the
answer must cite the chunks it used. The verifier receives the cited chunks as the sources for its
`unsupported_claim` and `sources` checks.

## 3. Ingestion and retrieval (module `knowledge`)

`KNW-20` **Chunking:** structure-aware. Split on headings, then pack paragraphs into chunks of about
350 tokens (max 500) with 15% overlap, carrying the heading path (`Diabetes > Blood tests > HbA1c`)
and language. A fixed-size chunker is kept as the comparison baseline in evaluation.

`KNW-21` **Embeddings:** `EmbeddingModel` port in the gateway, with a local multilingual adapter
(works without keys) and a provider adapter (Gemini embeddings on Vertex). The model must handle
Sinhala. The local multilingual model is the default when no provider key is configured. The
production default is chosen by measurement on the same Sinhala and English query set: Recall@5,
MRR, nDCG and context precision (Sinhala weighted first), with latency, cost and operational needs
recorded alongside. Embedding model
name and version are stored with every vector; changing the model triggers re-embedding.

`KNW-22` **Store:** PostgreSQL with the `pgvector` extension (no new infrastructure). Documents,
chunks and vectors persist in the database volume and its backups. A Qdrant adapter behind
`KnowledgeService` is the scale-out path (ADR-0026).
`knowledge_chunks(id, document_id, language, heading_path, text, tokens, embedding vector(n),
tsv tsvector, embedding_model)`, HNSW index on `embedding` (cosine), GIN on `tsv`.

`KNW-23` **Hybrid retrieval:** run vector search (top 20) and full-text search (top 20), merge with
Reciprocal Rank Fusion (k = 60), filter by language preference (patient language first, English
fallback), return top 5 with scores. Optional rerank (P1) with a decision-model `score` question.

`KNW-24` `KnowledgeService.retrieve(query, language, k=5) -> list[RetrievedChunk]` and
`KnowledgeService.ingest(path)`. Ingestion is a CLI command (`python main.py knowledge ingest`),
idempotent by content hash.

## 4. Caches

### Shared knowledge cache (one answer for everyone)
`KNW-30` Used only for intent `general_health_question`, keyed by a **canonical topic** and
language, with answers generated from the canonical topic question, never the user's wording
(poisoning defence, `SG-22`). Stored in `knowledge_answer_cache(id, topic_key,
language, normalized_question, question_embedding, answer, cited_chunk_ids, corpus_version,
prompt_version, model_id, verified_at, hits, created_at, expires_at)`.

`KNW-31` A hit is an exact match on the canonical topic in the same language.
**Semantic hits are off by default** (flag `shared_cache_semantic`). They may be switched on only
after the evaluation measures the false-match rate of the chosen similarity threshold (starting
candidate 0.92) on a labelled set of paraphrase and near-miss question pairs, and the rate is at most
0.5%. Safety rules (T0) run before every cache lookup, exact or semantic.

`KNW-32` An answer is written to the shared cache only if all are true: intent is
`general_health_question` with probability >= 0.9; the question and the answer contain **no personal
data** (PHI inspector finds nothing and no personal facts were in the prompt); the verifier allowed
it. Entries expire after 30 days or when `corpus_version` or `prompt_version` changes.

### User-scoped cache (one answer for one patient)
`KNW-33` Used for `my_record_question` and `report_explanation`. Stored in
`knowledge_user_cache(id, patient_id, language, normalized_question, question_embedding,
answer_encrypted, source_fingerprint, consent_fingerprint, created_at, expires_at)`. The answer is
encrypted with the patient's DEK (`SEC-22`).

`KNW-34` `source_fingerprint` is a hash of the IDs and versions of every resource used to answer.
`consent_fingerprint` is a hash of the requester's access decision. A hit is valid only if both still
match (so a new report, an edited medicine or a consent change invalidates it automatically), and the
requester passes `AccessPolicy` for those resources at hit time.

`KNW-35` User-cache entries are visible only to principals with access to the same resources. They
expire after 7 days and are deleted with the patient's key (crypto-shredding).

### Never cached
Symptom intake, urgency assessments, emergency messages, cross-role messages, doctor briefs and
replies, anything produced by the fake provider.

### Provider-side caching
`KNW-36` System prompts, tool definitions and corpus preambles stay byte-stable per prompt version so
provider prompt caching (Gemini context caching where available) applies.

## 5. Evaluation (added to `13-evaluation.md`)

| Metric | Definition | Target |
|---|---|---|
| Recall@5 | gold chunk in top 5 | >= 0.85 |
| MRR@10 | mean reciprocal rank of the first gold chunk | >= 0.70 |
| nDCG@10 | graded relevance | reported |
| Context precision | retrieved chunks judged relevant | >= 0.70 |
| Faithfulness | answer claims supported by cited chunks | >= 0.95 |
| Answer relevance | answer addresses the question | reported |
| Shared cache hit rate | on a replay of realistic question logs | reported |
| Semantic cache false-match rate | wrong answers served for near-miss question pairs at the chosen threshold | <= 0.5% before enabling |
| User cache stale hits | hits served after the sources changed | **0** |
| Tier mix | share of requests answered at T0, T1, T2, T3 | reported, with cost saved vs all-T3 |

Comparisons: structure-aware vs fixed-size chunking; local vs provider embeddings (quality, latency,
cost); hybrid vs vector only; with and without rerank. Query set: 120 labelled questions (60 si, 60 en) written by the team
with gold chunk IDs (data issue D06).
