# Benchmark Results - profile_abcd_real

> generated_at_utc: 2026-03-02T01:34:44+00:00
> mode: real execution (SQLiteClient.search_advanced + runtime profile env)

## Run Strategy

- dataset_scope: squad_v2_dev, beir_nfcorpus
- sample_size_requested: 10
- first_relevant_only: True
- extra_distractors: 80

## profile_a

- mode: `keyword`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 0.000 | 0.000 | 0.000 | 0.000 | 2.8 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.300 | 0.300 | 0.300 | 0.300 | 2.0 | 0.0% | - |

## profile_b

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 0.300 | 0.145 | 0.182 | 0.300 | 9.4 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.500 | 0.367 | 0.400 | 0.500 | 11.2 | 0.0% | - |

## profile_c

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 1.000 | 0.834 | 0.872 | 1.000 | 735.8 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.500 | 0.500 | 0.500 | 0.500 | 577.9 | 0.0% | - |

## profile_d

- mode: `hybrid`

| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| SQuAD v2 Dev | 10 | 90 | 1.000 | 0.845 | 0.882 | 1.000 | 2071.7 | 0.0% | - |
| BEIR NFCorpus | 10 | 90 | 0.600 | 0.550 | 0.563 | 0.600 | 2037.1 | 0.0% | - |

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
| SQuAD v2 Dev | 0.000 | 0.300 | 1.000 | 1.000 | 0.000 | 0.182 | 0.872 | 0.882 | 2.8 | 9.4 | 735.8 | 2071.7 | PASS |
| BEIR NFCorpus | 0.300 | 0.500 | 0.500 | 0.600 | 0.300 | 0.400 | 0.500 | 0.563 | 2.0 | 11.2 | 577.9 | 2037.1 | PASS |
