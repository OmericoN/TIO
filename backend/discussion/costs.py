"""Project shared indexing and per-listener spend from the pipeline's usage logs."""
import argparse
import json
import re
from pathlib import Path


def project(lines, rates, corpus_factor, listener_factor, microphone_seconds=0):
    shared = personal = ranking = 0.0
    count = 0
    for line in lines:
        usage = re.search(r"stage=(\w+) model=(\S+) input_tokens=(\d+) cached_input_tokens=(\d+) output_tokens=(\d+)", line)
        if usage:
            stage, name, total, cached, output = usage.groups()
            input_price, cached_price, output_price = rates["models"][name]
            amount = ((int(total) - int(cached)) * input_price + int(cached) * cached_price
                      + int(output) * output_price) / 1_000_000
            if stage in {"event_grouping", "event_evidence", "classification"}:
                shared += amount
            elif stage == "briefing_rundown":
                ranking += amount
            else:
                personal += amount
            count += 1
        chars = re.search(r"panel_tts characters=(\d+)", line)
        if chars:
            personal += int(chars[1]) * rates["tts_per_1000_chars"] / 1000
            count += 1
    if not count:
        raise ValueError("No provider usage in the supplied log.")
    personal += microphone_seconds * rates["stt_per_hour"] / 3600
    shared *= corpus_factor
    personal *= listener_factor
    ranking *= corpus_factor * listener_factor
    return {"shared_usd": shared, "listener_usd": personal + ranking,
            "full_usd": shared + personal + ranking,
            "10x_articles_same_listeners_usd": 10 * (shared + ranking) + personal,
            "10x_articles_and_listeners_usd": 10 * (shared + personal) + 100 * ranking}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("rates", type=Path)
    parser.add_argument("--corpus-factor", type=float, default=1)
    parser.add_argument("--listener-factor", type=float, default=1)
    parser.add_argument("--microphone-seconds", type=float, required=True)
    args = parser.parse_args()
    print(json.dumps(project(args.log.read_text(encoding="utf-8").splitlines(),
        json.loads(args.rates.read_text()), args.corpus_factor, args.listener_factor,
        args.microphone_seconds), indent=2))
