"""Command line for the TIO data pipeline.

    uv run python -m tio_data load [--dry-run]          # JSONL files -> Supabase
    uv run python -m tio_data embed check               # does the OpenAI key work with the embedding model?
    uv run python -m tio_data embed articles [--force]  # article embeddings
    uv run python -m tio_data embed users               # user interest embeddings
    uv run python -m tio_data summarize check           # is the local summary model up?
    uv run python -m tio_data summarize articles [--force] [--limit N] [--language de] [--workers 6]
    uv run python -m tio_data extract check             # is the local extraction model up?
    uv run python -m tio_data extract articles [--force] [--limit N] [--language de] [--workers 6]
"""

import argparse
import sys
from pathlib import Path

from tio_data.config import DATA_DIR


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up live in nohup/redirected logs
    parser = argparse.ArgumentParser(prog="tio_data", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    p_load = commands.add_parser("load", help="load the .jsonl news files into Supabase")
    p_load.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p_load.add_argument("--dry-run", action="store_true", help="read the files without touching the database")

    p_embed = commands.add_parser("embed", help="compute embeddings with OpenAI (text-embedding-3-large, 1536 dims)")
    targets = p_embed.add_subparsers(dest="target", required=True)
    targets.add_parser("check", help="test the OpenAI key and embedding model")
    p_articles = targets.add_parser("articles", help="embed articles")
    p_articles.add_argument("--force", action="store_true", help="re-embed every article")
    p_articles.add_argument("--batch-size", type=int, default=128)
    p_articles.add_argument("--limit", type=int, help="only embed this many, newest first")
    targets.add_parser("users", help="rebuild user interest embeddings")

    for name, help_text, verb in [
        ("summarize", "write English article summaries with the local Ollama LLM", "summarise"),
        ("extract", "extract subject, event, topics, people and entities with the local Ollama LLM", "extract"),
    ]:
        llm_targets = commands.add_parser(name, help=help_text).add_subparsers(dest="target", required=True)
        llm_targets.add_parser("check", help="test the OpenAI key and embedding model")
        p_llm = llm_targets.add_parser("articles", help=f"{verb} articles that have not been done yet")
        p_llm.add_argument("--force", action="store_true", help=f"{verb} every article again")
        p_llm.add_argument("--limit", type=int, help="only this many, newest first")
        p_llm.add_argument("--language", help="only this article language, e.g. en, de, nl")
        p_llm.add_argument("--model", help="Ollama model (default: SUMMARY_MODEL or qwen2.5:14b)")
        p_llm.add_argument("--workers", type=int, default=4, help="requests sent to Ollama at once")

    args = parser.parse_args()

    if args.command == "load":
        from tio_data.loader import load
        load(args.data_dir, args.dry_run)
        return

    if args.command in ("summarize", "extract"):
        from tio_data.config import SUMMARY_MODEL
        model = getattr(args, "model", None) or SUMMARY_MODEL
        if args.command == "summarize":
            from tio_data.summarize import OllamaSummarizer as Worker, check, summarize_articles as run
        else:
            from tio_data.extract import OllamaExtractor as Worker, check, extract_articles as run
        worker = Worker(model=model)
        try:
            if args.target == "check":
                check(worker)
            else:
                run(worker, args.force, args.limit, args.language, args.workers)
        except RuntimeError as err:
            sys.exit(str(err))
        return

    from tio_data import embed_jobs
    from tio_data.embeddings import OpenAIEmbedder

    embedder = OpenAIEmbedder()
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
