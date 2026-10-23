"""Turn the judge A/B's per-run JSON into one markdown table.

    PYTHONPATH=src uv run python scripts/summarize_judge_ab.py <output-dir>

Reads whatever `<arm>-run<N>.json` files exist, so it is useful while the runs
are still going and does not fail if one run crashed. Prints per-run numbers as
well as the mean: with run-to-run noise measured at roughly 7 on 89 targets
(ELI-1488), the spread between runs of the *same* arm is the only honest
yardstick for whether a gap between arms means anything.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ARMS = ("judge-off", "judge-on")
PROFILES = ("customers", "integrations")
SCOPES = (("external_only", "external-owned"), ("all_golden", "all golden"))


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0


def totals_for(path: Path) -> list[dict]:
    """Preserve every repeat in a harness output file."""
    try:
        runs = json.loads(path.read_text())["runs"]
    except OSError, ValueError, KeyError, IndexError:
        return []
    results = []
    for index, run in enumerate(runs, 1):
        total = totals_for_run(run)
        if total:
            total["name"] = f"{path.stem}/repeat-{index}"
            results.append(total)
    return results


def totals_for_run(run: dict) -> dict | None:
    """Recompute from per-vendor rows so failures remain visible."""
    ok = [v for v in run["per_vendor"] if "fatal" not in v]
    if not ok:
        return None
    out: dict = {"vendors": len(ok), "failed": len(run["per_vendor"]) - len(ok)}
    for profile in PROFILES:
        for scope, _ in SCOPES:
            golden = sum(v["profiles"][profile][scope]["golden"] for v in ok)
            found = sum(v["profiles"][profile][scope]["found"] for v in ok)
            hits = sum(v["profiles"][profile][scope]["hits"] for v in ok)
            precision = hits / found if found else 0.0
            recall = hits / golden if golden else 0.0
            out[(profile, scope)] = {
                "golden": golden,
                "found": found,
                "hits": hits,
                "precision": precision,
                "recall": recall,
                "f1": f1(precision, recall),
            }
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: summarize_judge_ab.py <output-dir>", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    runs: dict[str, list[dict]] = {}
    for arm in ARMS:
        paths = set(out_dir.glob(f"{arm}-run*.json"))
        if (out_dir / f"{arm}.json").exists():
            paths.add(out_dir / f"{arm}.json")
        for path in sorted(paths):
            runs.setdefault(arm, []).extend(totals_for(path))

    if not any(runs.values()):
        print(f"No usable run files in {out_dir}.")
        return 1

    print("# Judge A/B — external discovery only\n")
    for arm in ARMS:
        got = runs.get(arm, [])
        vendors = {r["vendors"] for r in got}
        print(f"- **{arm}**: {len(got)} run(s), vendors per run: {sorted(vendors)}")
    print()

    for profile in PROFILES:
        print(f"## {profile}\n")
        for scope, scope_label in SCOPES:
            golden = next((r[(profile, scope)]["golden"] for got in runs.values() for r in got), 0)
            print(f"### {scope_label} (golden = {golden})\n")
            print("| arm | run | found | hits | precision | recall | F1 |")
            print("|---|---|---|---|---|---|---|")
            for arm in ARMS:
                f1s = []
                for r in runs.get(arm, []):
                    s = r[(profile, scope)]
                    f1s.append(s["f1"])
                    print(
                        f"| {arm} | {r['name'].split('-run')[-1]} | {s['found']} | {s['hits']} "
                        f"| {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} |"
                    )
                if f1s:
                    spread = max(f1s) - min(f1s)
                    mean = statistics.fmean(f1s)
                    print(f"| **{arm}** | **mean** | | | | | **{mean:.3f}** |")
                    print(f"| {arm} | spread | | | | | {spread:.3f} |")
            print()
            off = [r[(profile, scope)]["f1"] for r in runs.get("judge-off", [])]
            on = [r[(profile, scope)]["f1"] for r in runs.get("judge-on", [])]
            if off and on:
                gap = statistics.fmean(on) - statistics.fmean(off)
                noise = max(
                    (max(off) - min(off)) if len(off) > 1 else 0.0,
                    (max(on) - min(on)) if len(on) > 1 else 0.0,
                )
                verdict = (
                    "inside the noise — not a result"
                    if abs(gap) <= noise
                    else ("judge helps" if gap > 0 else "judge hurts")
                )
                print(
                    f"**F1 gap (on − off): {gap:+.3f}**, widest within-arm spread {noise:.3f} "
                    f"→ {verdict}.\n"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
