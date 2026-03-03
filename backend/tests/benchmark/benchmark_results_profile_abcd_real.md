# Benchmark Results - profile_abcd_real

> generated_at_utc: 2026-03-03T10:25:44+00:00
> mode: real execution (SQLiteClient.search_advanced + runtime profile env)

## Run Strategy

- dataset_scope: squad_v2_dev
- sample_size_requested: 1
- first_relevant_only: True
- extra_distractors: 20

## profile_a

- mode: `keyword`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 0.000 | 0.000 | 0.000 | 0.000 | 2.2 | 0.0% | - |

## profile_b

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 5.5 | 0.0% | - |

## profile_c

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 3167.1 | 100.0% | embedding_fallback_hash,embedding_request_failed |

## profile_d

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 1 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 1732.1 | 0.0% | - |

## Phase 6 Gate (Profile D)

- overall_valid: true
- invalid_reasons: (none)

| Dataset | Valid | Invalid Reasons |
|---|---|---|
| SQuAD v2 Dev | PASS | - |

## A/B/C/D Comparison

| Dataset | A HR@10 | B HR@10 | C HR@10 | D HR@10 | A NDCG@10 | B NDCG@10 | C NDCG@10 | D NDCG@10 | A p95 | B p95 | C p95 | D p95 | D Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 0.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 2.2 | 5.5 | 3167.1 | 1732.1 | PASS |
