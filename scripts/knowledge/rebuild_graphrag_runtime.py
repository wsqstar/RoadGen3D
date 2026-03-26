from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.knowledge.graphrag import GraphRagKnowledgeRetriever  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild the official GraphRAG runtime artifacts.")
    parser.add_argument(
        "--project-dir",
        default=str((ROOT / "knowledge" / "graphRAG").resolve()),
        help="Path to the graphRAG project directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force a retry even if the last build failed with the same inputs/settings.",
    )
    parser.add_argument(
        "--query",
        default="minimum sidewalk width near transit stops",
        help="Optional smoke-test query to run after a successful rebuild.",
    )
    parser.add_argument(
        "--skip-query",
        action="store_true",
        help="Skip the post-build smoke-test query.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    retriever = GraphRagKnowledgeRetriever(project_dir=args.project_dir)
    log_path = (Path(args.project_dir) / "graphrag_quickstart" / "logs" / "indexing-engine.log").resolve()

    print("before", flush=True)
    print(json.dumps(retriever.describe().to_dict(), ensure_ascii=False, indent=2), flush=True)
    print(f"log_path: {log_path}", flush=True)
    print("starting official GraphRAG rebuild...", flush=True)

    try:
        ensure_info = retriever.ensure_runtime_artifacts(force=bool(args.force))
    except Exception as exc:
        print("ensure_error", flush=True)
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "log_path": str(log_path),
                    "status": retriever.describe().to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 1

    print("ensure", flush=True)
    print(json.dumps(ensure_info, ensure_ascii=False, indent=2), flush=True)

    print("after", flush=True)
    print(json.dumps(retriever.describe().to_dict(), ensure_ascii=False, indent=2), flush=True)

    if args.skip_query:
        return 0

    hits = retriever.search(args.query, topk=3)
    print("hits", flush=True)
    print(
        json.dumps(
            [
                {
                    "chunk_id": item.chunk.chunk_id,
                    "score": item.score,
                    "section_title": item.chunk.section_title,
                    "source_path": item.chunk.source_path,
                }
                for item in hits
            ],
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
