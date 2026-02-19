#!/usr/bin/env python3
"""Run real A/B/C/D benchmark and emit artifacts."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from helpers.profile_abcd_real_runner import (
    REAL_PROFILE_CD_MARKDOWN_ARTIFACT,
    REAL_PROFILE_JSON_ARTIFACT,
    REAL_PROFILE_MARKDOWN_ARTIFACT,
    build_profile_abcd_real_metrics,
    render_abcd_sota_analysis_markdown,
    write_profile_abcd_real_artifacts,
)


def _default_analysis_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "benchmark_abcd_real_analysis_2026_02.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run real benchmark for profiles A/B/C/D. "
            "Profile C/D uses API embedding and optional reranker based on env."
        )
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=30,
        help="Effective query count per dataset (<= bucket size 100/200/500). Default: 30",
    )
    parser.add_argument(
        "--datasets",
        type=str,
        default="squad_v2_dev,beir_nfcorpus",
        help="Comma-separated dataset keys. Supported: squad_v2_dev,beir_nfcorpus",
    )
    parser.add_argument(
        "--extra-distractors",
        type=int,
        default=200,
        help="Extra non-relevant corpus docs per dataset. Default: 200",
    )
    parser.add_argument(
        "--all-relevant",
        action="store_true",
        help="Use all relevant doc IDs from labels (default uses first relevant only).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REAL_PROFILE_JSON_ARTIFACT,
        help=f"Output JSON path. Default: {REAL_PROFILE_JSON_ARTIFACT}",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=REAL_PROFILE_MARKDOWN_ARTIFACT,
        help=f"Output full markdown path. Default: {REAL_PROFILE_MARKDOWN_ARTIFACT}",
    )
    parser.add_argument(
        "--output-cd-md",
        type=Path,
        default=REAL_PROFILE_CD_MARKDOWN_ARTIFACT,
        help=f"Output C/D markdown path. Default: {REAL_PROFILE_CD_MARKDOWN_ARTIFACT}",
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        default=_default_analysis_path(),
        help="Output analysis markdown path.",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    dataset_keys = [item.strip() for item in args.datasets.split(",") if item.strip()]
    payload = await build_profile_abcd_real_metrics(
        sample_size=int(args.sample_size),
        dataset_keys=dataset_keys,
        first_relevant_only=not bool(args.all_relevant),
        extra_distractors=int(args.extra_distractors),
    )
    artifact_paths = write_profile_abcd_real_artifacts(
        payload,
        json_path=args.output_json,
        markdown_path=args.output_md,
        cd_markdown_path=args.output_cd_md,
    )
    analysis_markdown = render_abcd_sota_analysis_markdown(payload)
    args.analysis_output.parent.mkdir(parents=True, exist_ok=True)
    args.analysis_output.write_text(analysis_markdown, encoding="utf-8")

    print(f"[benchmark] generated json: {artifact_paths['json']}")
    print(f"[benchmark] generated md: {artifact_paths['markdown']}")
    print(f"[benchmark] generated cd md: {artifact_paths['cd_markdown']}")
    print(f"[benchmark] generated analysis: {args.analysis_output}")


def main() -> int:
    args = parse_args()
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
