#!/usr/bin/env python3
"""Compare JSON snapshots exactly except for explicitly bounded numeric noise."""

import argparse
import json
import math
from pathlib import Path


def compare_values(before, after, path="$", rel_tol=1e-12, abs_tol=1e-12):
    differences, tolerated = [], []
    if type(before) is not type(after):
        return [(path, before, after)], tolerated
    if isinstance(before, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}"
            if key not in before or key not in after:
                differences.append((child, before.get(key, "<MISSING>"), after.get(key, "<MISSING>")))
            else:
                d, t = compare_values(before[key], after[key], child, rel_tol, abs_tol)
                differences.extend(d)
                tolerated.extend(t)
    elif isinstance(before, list):
        if len(before) != len(after):
            differences.append((f"{path}.length", len(before), len(after)))
        for index, (left, right) in enumerate(zip(before, after)):
            d, t = compare_values(left, right, f"{path}[{index}]", rel_tol, abs_tol)
            differences.extend(d)
            tolerated.extend(t)
    elif isinstance(before, (int, float)) and not isinstance(before, bool):
        if before != after:
            if math.isclose(before, after, rel_tol=rel_tol, abs_tol=abs_tol):
                tolerated.append((path, before, after, abs(before - after)))
            else:
                differences.append((path, before, after))
    elif before != after:
        differences.append((path, before, after))
    return differences, tolerated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--rel-tol", type=float, default=1e-12)
    parser.add_argument("--abs-tol", type=float, default=1e-12)
    args = parser.parse_args()
    old, new = json.loads(args.before.read_text()), json.loads(args.after.read_text())
    differences, tolerated = compare_values(old, new, rel_tol=args.rel_tol, abs_tol=args.abs_tol)
    for path, left, right, delta in tolerated:
        print(f"WITHIN_TOLERANCE {path}: {left!r} vs {right!r}; abs_delta={delta:.17g}")
    for path, left, right in differences:
        print(f"DIFFERENCE {path}: {left!r} vs {right!r}")
    print(f"RESULT differences={len(differences)} tolerance_only={len(tolerated)}")
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
