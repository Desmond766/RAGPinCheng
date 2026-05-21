"""Diff two eval run logs by item_id.

Shows what fixed (was MISS, now HIT) and what regressed (was HIT, now
MISS) between a baseline and a candidate run. Also reports rank shifts
on items that hit in both.

Usage:
    python scripts/diff_eval_runs.py <baseline.jsonl> <candidate.jsonl>
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _load(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[r["item_id"]] = r
    return out


def _status(rec: dict) -> str:
    if rec.get("kind") == "no_answer":
        return "OK" if rec.get("compliant") else "MISS"
    return "HIT" if rec.get("hit_rank") is not None else "MISS"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline", type=Path)
    p.add_argument("candidate", type=Path)
    args = p.parse_args()

    a = _load(args.baseline)
    b = _load(args.candidate)

    common = sorted(set(a) & set(b))
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))

    fixed: list[tuple[str, dict, dict]] = []   # MISS → HIT
    regressed: list[tuple[str, dict, dict]] = []  # HIT → MISS
    rank_shifts: list[tuple[str, int, int]] = []
    same: int = 0

    for iid in common:
        ra, rb = a[iid], b[iid]
        sa, sb = _status(ra), _status(rb)
        if sa == "MISS" and sb == "HIT":
            fixed.append((iid, ra, rb))
        elif sa == "HIT" and sb == "MISS":
            regressed.append((iid, ra, rb))
        elif sa == "OK" and sb != "OK":
            regressed.append((iid, ra, rb))
        elif sa != "OK" and sb == "OK":
            fixed.append((iid, ra, rb))
        else:
            same += 1
            if "hit_rank" in ra and "hit_rank" in rb:
                ha, hb = ra.get("hit_rank"), rb.get("hit_rank")
                if ha is not None and hb is not None and ha != hb:
                    rank_shifts.append((iid, ha, hb))

    # Headline counts.
    print(f"baseline:  {args.baseline}  ({len(a)} items)")
    print(f"candidate: {args.candidate}  ({len(b)} items)")
    if only_a or only_b:
        print(f"items only in baseline: {len(only_a)}")
        print(f"items only in candidate: {len(only_b)}")

    print()
    print(f"  fixed (MISS → HIT): {len(fixed)}")
    print(f"  regressed (HIT → MISS): {len(regressed)}")
    print(f"  unchanged: {same}")
    print(f"  rank shifts (both HIT): {len(rank_shifts)}")

    if fixed:
        print()
        print("== Fixed ==")
        for iid, ra, rb in fixed:
            print(f"  {iid:<32} [{ra.get('kind','?'):<14}] "
                  f"was rank={ra.get('hit_rank')} now rank={rb.get('hit_rank')}")

    if regressed:
        print()
        print("== Regressed ==")
        for iid, ra, rb in regressed:
            print(f"  {iid:<32} [{ra.get('kind','?'):<14}] "
                  f"was rank={ra.get('hit_rank')} now rank={rb.get('hit_rank')}")

    if rank_shifts:
        # Bucket: improvements (smaller rank) vs degradations
        better = [s for s in rank_shifts if s[2] < s[1]]
        worse = [s for s in rank_shifts if s[2] > s[1]]
        print()
        print(f"== Rank shifts (n={len(rank_shifts)}: {len(better)} better, {len(worse)} worse) ==")
        for iid, ha, hb in sorted(rank_shifts, key=lambda x: x[2]-x[1]):
            arrow = "↑" if hb < ha else "↓"
            print(f"  {iid:<32} {ha} {arrow} {hb}")

    # Per-kind summary.
    by_kind_a: dict[str, list[int]] = defaultdict(list)  # hit_rank values (None for miss)
    by_kind_b: dict[str, list[int]] = defaultdict(list)
    for iid in common:
        ra, rb = a[iid], b[iid]
        k = ra.get("kind") or rb.get("kind") or "unknown"
        if k == "no_answer":
            by_kind_a[k].append(1 if ra.get("compliant") else 0)
            by_kind_b[k].append(1 if rb.get("compliant") else 0)
        else:
            by_kind_a[k].append(ra.get("hit_rank") or 0)
            by_kind_b[k].append(rb.get("hit_rank") or 0)

    print()
    print("== Per-kind hit rate change ==")
    print(f"{'kind':<16} {'n':>4} {'base R@5':>10} {'cand R@5':>10} {'Δ':>8}")
    print("-" * 50)
    for kind in sorted(by_kind_a):
        va, vb = by_kind_a[kind], by_kind_b[kind]
        if kind == "no_answer":
            ra = sum(va) / max(len(va), 1)
            rb = sum(vb) / max(len(vb), 1)
        else:
            ra = sum(1 for x in va if 0 < x <= 5) / max(len(va), 1)
            rb = sum(1 for x in vb if 0 < x <= 5) / max(len(vb), 1)
        delta = rb - ra
        marker = ("+" if delta > 0 else "") + f"{delta:.3f}"
        print(f"{kind:<16} {len(va):>4} {ra:>10.3f} {rb:>10.3f} {marker:>8}")


if __name__ == "__main__":
    main()
