"""Moderation accuracy on labeled chat messages (P1, ARCHITECTURE.md section 12).

Runs the same prompts as production: prefilter in batches of 10 → moderator for flagged messages
→ policy.guard_moderation(). Unflagged messages count as 'acquit'. No database needed.

    cd backend
    python scripts/eval_moderation.py                    # all 30 messages
    python scripts/eval_moderation.py --skip-prefilter   # moderator on every message
    python scripts/eval_moderation.py --limit 10 --out eval.json
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app import prompts  # noqa: E402
from app.llm import PROVIDER, LLMError, chat_json, model_for  # noqa: E402
from app.schemas import ModerationDecision, PrefilterResult  # noqa: E402

try:
    from app.policy import guard_moderation  # noqa: E402  (owner A)
except ImportError:
    guard_moderation = None

LABELS = ["acquit", "timeout", "ban"]
PREFILTER_BATCH = 10


def run_prefilter(items: list[dict], rules: str) -> tuple[dict[int, str], int]:
    flagged, errors = {}, 0
    for i in range(0, len(items), PREFILTER_BATCH):
        batch = items[i:i + PREFILTER_BATCH]
        try:
            res = chat_json("prefilter", prompts.prefilter_system(rules),
                            prompts.prefilter_user({"id": m["id"], "user": m["username"], "text": m["text"]}
                                                   for m in batch),
                            PrefilterResult, 0.1)
        except LLMError as e:              # production is fail-open: the batch passes
            print(f"  prefilter error: {e}", file=sys.stderr)
            errors += 1
            continue
        ids = {m["id"] for m in batch}
        flagged.update({f.id: f.reason for f in res.flagged if f.id in ids})
    return flagged, errors


def run_moderator(item: dict, channel: dict, prefilter_reason: str) -> tuple[str, dict]:
    t0 = time.monotonic()
    try:
        d = chat_json(
            "moderator",
            prompts.moderator_system(channel["name"], channel["stream_context"], channel["rules"]),
            prompts.moderator_user(item.get("chat_window", []), item["username"], item["text"], prefilter_reason,
                                   item.get("user_history", []), item.get("past_incidents", [])),
            ModerationDecision, 0.2,
        )
    except LLMError as e:
        return "acquit", {"error": str(e), "latency_s": time.monotonic() - t0}   # production default
    raw = d.model_dump()
    if guard_moderation:
        d = guard_moderation(d)
    return d.decision, {"model": raw, "guarded": d.model_dump(), "latency_s": round(time.monotonic() - t0, 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=str(BACKEND / "seed" / "labeled_messages.json"))
    ap.add_argument("--skip-prefilter", action="store_true", help="send every message to the moderator")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", help="write per-message results as JSON")
    args = ap.parse_args()

    data = json.loads(Path(args.file).read_text())
    channel, items = data["channel"], data["items"]
    if args.limit:
        items = items[:args.limit]
    print(f"provider={PROVIDER} prefilter={model_for('prefilter')} moderator={model_for('moderator')} "
          f"messages={len(items)}")
    if guard_moderation is None:
        print("WARNING: app/policy.py not found — raw moderator decisions, no guards")

    t0 = time.monotonic()
    if args.skip_prefilter:
        flagged, pf_errors = {m["id"]: "prefilter skipped (eval)" for m in items}, 0
    else:
        flagged, pf_errors = run_prefilter(items, channel["rules"])

    results, latencies, mod_errors = [], [], 0
    for it in items:
        if it["id"] in flagged:
            pred, info = run_moderator(it, channel, flagged[it["id"]])
            latencies.append(info["latency_s"])
            mod_errors += "error" in info
        else:
            pred, info = "acquit", {"prefilter": "passed"}
        results.append({"id": it["id"], "text": it["text"], "expected": it["expected"], "predicted": pred,
                        "flagged": it["id"] in flagged, **info})
        mark = "✓" if pred == it["expected"] else "✗"
        print(f"  {mark} #{it['id']:<3} expected={it['expected']:<8} got={pred:<8} {it['text'][:60]}")

    total = len(results)
    correct = sum(r["expected"] == r["predicted"] for r in results)
    false_bans = sum(r["predicted"] == "ban" and r["expected"] != "ban" for r in results)
    punished_innocent = sum(r["predicted"] != "acquit" and r["expected"] == "acquit" for r in results)
    missed = sum(r["predicted"] == "acquit" and r["expected"] != "acquit" for r in results)
    violations = [r for r in results if r["expected"] != "acquit"]
    pf_recall = sum(r["flagged"] for r in violations) / max(len(violations), 1)
    cm = Counter((r["expected"], r["predicted"]) for r in results)

    print(f"\nТочність: {correct / total:.0%} ({correct}/{total})")
    print(f"Хибних банів: {false_bans}   Покарано невинних: {punished_innocent}   Пропущено порушень: {missed}")
    if not args.skip_prefilter:
        print(f"Префільтр: позначив {len(flagged)}/{total}, recall порушень {pf_recall:.0%}, помилок {pf_errors}")
    if latencies:
        print(f"Модератор: {len(latencies)} викликів, середньо {sum(latencies) / len(latencies):.1f} с, "
              f"помилок {mod_errors}")
    print(f"Загальний час: {time.monotonic() - t0:.0f} с")
    print("\nМатриця помилок (рядки — очікуване, стовпці — рішення):")
    print(" " * 10 + "".join(f"{lbl:>9}" for lbl in LABELS))
    for e in LABELS:
        print(f"{e:<10}" + "".join(f"{cm[(e, p)]:>9}" for p in LABELS))

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nДеталі: {args.out}")


if __name__ == "__main__":
    main()
