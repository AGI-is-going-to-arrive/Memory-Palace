# Memory Palace — Retrieval Benchmark Results

> Generated: 2026-02-17 23:16:01
> Embedding: `hash` (dim=64)
> Reranker: `false`

> **Note**: Hash-based embedding is deterministic but not semantic.
> Keyword results reflect true BM25/FTS5 quality. Semantic/hybrid
> results show pipeline functionality, not real language model quality.

---

## MS MARCO (passages)

### Retrieval Quality

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Hit Rate @5 | 0.333 | 0.833 | 0.833 |
| Hit Rate @10 | 0.333 | 0.867 | 0.867 |
| MRR | 0.333 | 0.624 | 0.658 |
| NDCG@10 | 0.333 | 0.671 | 0.696 |
| Precision@5 | 0.067 | 0.180 | 0.180 |
| Recall@10 | 0.333 | 0.850 | 0.850 |

### Latency (ms)

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Avg | 1.3 | 2.7 | 3.2 |
| p50 | 1.2 | 2.7 | 3.4 |
| p95 | 2.1 | 3.1 | 3.7 |
| p99 | 2.8 | 3.2 | 3.7 |

### Degradation

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Queries | 30 | 30 | 30 |
| Degraded | 0 | 0 | 0 |
| Rate | 0.0% | 0.0% | 0.0% |

---

## BEIR NFCorpus (medical)

### Retrieval Quality

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Hit Rate @5 | 0.300 | 0.950 | 0.950 |
| Hit Rate @10 | 0.300 | 1.000 | 1.000 |
| MRR | 0.300 | 0.770 | 0.828 |
| NDCG@10 | 0.300 | 0.807 | 0.850 |
| Precision@5 | 0.060 | 0.200 | 0.200 |
| Recall@10 | 0.300 | 0.975 | 0.975 |

### Latency (ms)

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Avg | 1.6 | 3.9 | 3.8 |
| p50 | 1.6 | 3.8 | 4.1 |
| p95 | 2.6 | 6.0 | 4.7 |
| p99 | 2.6 | 6.0 | 4.7 |

### Degradation

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Queries | 20 | 20 | 20 |
| Degraded | 0 | 0 | 0 |
| Rate | 0.0% | 0.0% | 0.0% |

---

## SQuAD 2.0 (QA)

### Retrieval Quality

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Hit Rate @5 | 0.150 | 0.850 | 0.850 |
| Hit Rate @10 | 0.150 | 1.000 | 1.000 |
| MRR | 0.150 | 0.757 | 0.765 |
| NDCG@10 | 0.150 | 0.815 | 0.822 |
| Precision@5 | 0.030 | 0.170 | 0.170 |
| Recall@10 | 0.150 | 1.000 | 1.000 |

### Latency (ms)

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Avg | 1.3 | 2.8 | 3.1 |
| p50 | 1.2 | 2.7 | 3.2 |
| p95 | 3.0 | 3.9 | 3.9 |
| p99 | 3.0 | 3.9 | 3.9 |

### Degradation

| Metric | keyword | semantic | hybrid |
|---|---|---|---|
| Queries | 20 | 20 | 20 |
| Degraded | 0 | 0 | 0 |
| Rate | 0.0% | 0.0% | 0.0% |

---

## Best Mode Summary

| Dataset | Best HR@10 | Best MRR | Best NDCG@10 |
|---|---|---|---|
| MS MARCO (passages) | semantic (0.867) | hybrid (0.658) | hybrid (0.696) |
| BEIR NFCorpus (medical) | semantic (1.000) | hybrid (0.828) | hybrid (0.850) |
| SQuAD 2.0 (QA) | semantic (1.000) | hybrid (0.765) | hybrid (0.822) |
