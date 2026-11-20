"""Command line for the TIO data pipeline.

    uv run python -m tio_data load [--dry-run]          # JSONL files -> Supabase
    uv run python -m tio_data embed check               # is Ollama up with the right model?
    uv run python -m tio_data embed articles [--force]  # article embeddings
    uv run python -m tio_data embed users               # user interest embeddings
"""

import argparse
import sys
from pathlib import Path

from tio_data.config import DATA_DIR


def main() -> None:
    parser = argparse.ArgumentParser(prog="tio_data", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    p_load = commands.add_parser("load", help="load the .jsonl news files into Supabase")
    p_load.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p_load.add_argument("--dry-run", action="store_true", help="read the files without touching the database")

    p_embed = commands.add_parser("embed", help="compute embeddings with the local Ollama model")
    targets = p_embed.add_subparsers(dest="target", required=True)
    targets.add_parser("check", help="test the Ollama connection and model")
    p_articles = targets.add_parser("articles", help="embed articles")
    p_articles.add_argument("--force", action="store_true", help="re-embed every article")
    p_articles.add_argument("--batch-size", type=int, default=32)
    p_articles.add_argument("--limit", type=int, help="only embed this many, newest first")
    targets.add_parser("users", help="rebuild user interest embeddings")

    args = parser.parse_args()

    if args.command == "load":
        from tio_data.loader import load
        load(args.data_dir, args.dry_run)
        return

    from tio_data import embed_jobs
    from tio_data.embeddings import OllamaEmbedder

    embedder = OllamaEmbedder()
    try:
        if args.target == "check":
            embed_jobs.check(embedder)
        elif args.target == "articles":
            embed_jobs.embed_articles(embedder, args.batch_size, args.force, args.limit)
        else:
            embed_jobs.embed_users(embedder)
    except RuntimeError as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
