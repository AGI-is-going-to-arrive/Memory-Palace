# Benchmark Results - profile_abcd_real

> generated_at_utc: 2026-02-18T21:22:48+00:00
> mode: real execution (SQLiteClient.search_advanced + runtime profile env)

## Run Strategy

- dataset_scope: squad_v2_dev, beir_nfcorpus
- sample_size_requested: 8
- first_relevant_only: True
- extra_distractors: 10

## profile_a

- mode: `keyword`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 8 | 18 | 0.000 | 0.000 | 0.000 | 0.000 | 1.8 | 0.0% | - |
| BEIR NFCorpus | 8 | 18 | 0.250 | 0.250 | 0.250 | 0.250 | 1.7 | 0.0% | - |

## profile_b

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 8 | 18 | 0.625 | 0.302 | 0.383 | 0.625 | 4.9 | 0.0% | - |
| BEIR NFCorpus | 8 | 18 | 0.750 | 0.478 | 0.542 | 0.750 | 5.0 | 0.0% | - |

## profile_c

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 8 | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 665.1 | 0.0% | - |
| BEIR NFCorpus | 8 | 18 | 0.750 | 0.567 | 0.611 | 0.750 | 454.4 | 0.0% | - |

## profile_d

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 8 | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 2078.4 | 0.0% | - |
| BEIR NFCorpus | 8 | 18 | 0.750 | 0.650 | 0.673 | 0.750 | 2365.0 | 0.0% | - |

## Phase 6 Gate (Profile D)

- overall_valid: true
- invalid_reasons: (none)

| Dataset | Valid | Invalid Reasons |
|---|---|---|
| SQuAD v2 Dev | PASS | - |
| BEIR NFCorpus | PASS | - |

## A/B/C/D Comparison

| Dataset | A HR@10 | B HR@10 | C HR@10 | D HR@10 | A NDCG@10 | B NDCG@10 | C NDCG@10 | D NDCG@10 | A p95 | B p95 | C p95 | D p95 | D Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 0.000 | 0.625 | 1.000 | 1.000 | 0.000 | 0.383 | 1.000 | 1.000 | 1.8 | 4.9 | 665.1 | 2078.4 | PASS |
| BEIR NFCorpus | 0.250 | 0.750 | 0.750 | 0.750 | 0.250 | 0.542 | 0.611 | 0.673 | 1.7 | 5.0 | 454.4 | 2365.0 | PASS |
