# Benchmark A/B/C/D Real Run vs 2026-02 External References

## 1) Run scope (real chain)

- Run timestamp (UTC): `2026-02-18T21:22:48+00:00`
- Command:
  - `cd Memory-Palace/backend && .venv/bin/python tests/benchmark/run_profile_abcd_real.py --sample-size 8 --datasets squad_v2_dev,beir_nfcorpus --extra-distractors 10`
- Data scope: `squad_v2_dev`, `beir_nfcorpus`
- Retrieval mode:
  - A: keyword
  - B: hybrid + hash embedding
  - C: hybrid + API embedding
  - D: hybrid + API embedding + reranker
- Phase6 gate: `valid=true`, `invalid_reasons=[]`

Source artifacts:
- `Memory-Palace/backend/tests/benchmark/profile_abcd_real_metrics.json`
- `Memory-Palace/backend/tests/benchmark/benchmark_results_profile_abcd_real.md`
- `Memory-Palace/backend/tests/benchmark/benchmark_results_profile_cd_real.md`

## 2) Real measured results (dataset-average)

| Profile | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) |
|---|---:|---:|---:|---:|---:|
| A | 0.1250 | 0.1250 | 0.1250 | 0.1250 | 1.763 |
| B | 0.6875 | 0.3901 | 0.4622 | 0.6875 | 4.970 |
| C | 0.8750 | 0.7833 | 0.8054 | 0.8750 | 559.783 |
| D | 0.8750 | 0.8250 | 0.8367 | 0.8750 | 2221.674 |

C -> D delta (dataset-average):
- NDCG@10: `+0.03125`
- p95 latency: `+1661.891 ms` (`3.969x`)

## 3) 2026-02 external reference anchors

### 3.1 MTEB official results repository (retrieval metric: nDCG@10)

Official sources:
- [MTEB results dataset (Hugging Face)](https://huggingface.co/datasets/mteb/results/tree/main)
- [MTEB leaderboard code (task list + metric mapping)](https://huggingface.co/spaces/mteb/leaderboard/raw/c1f5045911605113a891d8fa42eb672483fab3b2/app.py)

Using English retrieval task set in the leaderboard code (`TASK_LIST_RETRIEVAL`, 15 tasks), we computed means from raw model result JSON files in `mteb/results`:

| Model | Covered tasks | Mean nDCG@10 |
|---|---:|---:|
| `baseline__bm25s` | 15 | 0.4147 |
| `sentence-transformers__all-MiniLM-L6-v2` | 14 | 0.4393 |
| `BAAI__bge-large-en-v1.5` | 5 | 0.5491 |
| `openai__text-embedding-3-large` | 14 | 0.5652 |
| `voyageai__voyage-large-2-instruct` | 15 | 0.5730 |
| `Alibaba-NLP__gte-Qwen1.5-7B-instruct` | 14 | 0.5728 |

Interpretation anchors (2026-02 snapshot):
- BM25 baseline anchor: `~0.41`
- Modern dense embedding range (representative set above): `~0.55-0.57`
- Representative dense mean (BGE/OpenAI/Voyage/GTE): `~0.565`

### 3.2 2026 reranker paper on BEIR subsets (NDCG@10)

Source:
- [When Vision Meets Texts in Listwise Reranking (arXiv:2601.20623v1)](https://arxiv.org/html/2601.20623v1)

Table 3 (7 BEIR subsets) reports:
- BM25 Avg: `43.74` (0.4374)
- Non-reasoning rerankers Avg: `52.26-54.13` (0.5226-0.5413)
- Reasoning rerankers Avg: `53.60-54.61` (0.5360-0.5461)

## 4) A/B/C/D vs external anchors

- A (0.1250): far below public BM25 baseline anchor (`~0.41`).
- B (0.4622): above BM25 anchor, near low/mid dense range, but below modern dense representative mean (`~0.565`).
- C (0.8054) and D (0.8367): numerically above the external anchors listed above.

## 5) Critical comparability boundary (must-read)

These C/D numbers are **not directly claimable as public SOTA** because:
- Internal run uses only two datasets (`squad_v2_dev`, `beir_nfcorpus`) with small sample (`sample-size=8`).
- `first_relevant_only=true` and controlled distractor setup (`extra-distractors=10`).
- External references aggregate broader tasks and larger evaluation pools.

Therefore, current C/D results are valid as **internal regression signal** (real API chain confirmed), not as external leaderboard claim.

## 6) Conclusion

- Real C/D chain is landed and verified (no fallback pseudo-pass in Phase6 gate).
- D improves ranking quality over C (`+0.03125` NDCG@10) with large latency cost (`+1661.891ms`, `3.969x` p95).
- Against 2026-02 public anchors, current internal C/D scores are strong, but external SOTA claim requires larger, standardized benchmark runs.
