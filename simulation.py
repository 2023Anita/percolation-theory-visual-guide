#!/usr/bin/env python3
"""Reproducible square-lattice site-percolation simulation and example data."""

from __future__ import annotations

import csv
import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy import ndimage

    SCIPY_VERSION: str | None = __import__("scipy").__version__
except ImportError:
    ndimage = None
    SCIPY_VERSION = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
BASE_SEED = 20261006
SIZES = (32, 64, 128)
P_VALUES = tuple(round(0.50 + i * 0.01, 2) for i in range(19))
COMPARISON_P = (0.58, 0.60)
TRIALS = 500
COMPARISON_TRIALS = 1000
Z_975 = 1.959963984540054

FOUR_NEIGHBORS = np.array(
    [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8
)


def derive_seed(stream: int, n: int, p: float) -> int:
    """Return a stable independent seed for one simulation condition."""
    p_milli = int(round(p * 1000))
    words = np.random.SeedSequence([BASE_SEED, stream, n, p_milli]).generate_state(
        2, dtype=np.uint32
    )
    return int(words[0]) | (int(words[1]) << 32)


def spans_left_to_right(open_cells: np.ndarray) -> bool:
    """Test 4-neighbor left-to-right connectivity with open outer boundaries."""
    if ndimage is not None:
        labels, _ = ndimage.label(open_cells, structure=FOUR_NEIGHBORS)
        left = np.unique(labels[:, 0])
        right = np.unique(labels[:, -1])
        return bool(np.intersect1d(left[left > 0], right[right > 0]).size)

    reachable = np.zeros_like(open_cells, dtype=bool)
    reachable[:, 0] = open_cells[:, 0]
    if np.any(reachable[:, -1]):
        return True
    while True:
        expanded = reachable.copy()
        expanded[1:, :] |= reachable[:-1, :]
        expanded[:-1, :] |= reachable[1:, :]
        expanded[:, 1:] |= reachable[:, :-1]
        expanded[:, :-1] |= reachable[:, 1:]
        expanded &= open_cells
        if np.any(expanded[:, -1]):
            return True
        if np.array_equal(expanded, reachable):
            break
        reachable = expanded
    return False


def bfs_reachable_and_path(
    open_cells: np.ndarray,
) -> tuple[list[list[int]], list[list[int]]]:
    """Return every cell reachable from the left edge and one crossing path."""
    n = open_cells.shape[0]
    parent = np.full((n, n), -1, dtype=np.int32)
    queue: deque[tuple[int, int]] = deque()
    for r in range(n):
        if open_cells[r, 0]:
            parent[r, 0] = -2
            queue.append((r, 0))

    crossing_end: tuple[int, int] | None = None
    while queue:
        r, c = queue.popleft()
        if c == n - 1 and crossing_end is None:
            crossing_end = (r, c)
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if (
                0 <= nr < n
                and 0 <= nc < n
                and open_cells[nr, nc]
                and parent[nr, nc] == -1
            ):
                parent[nr, nc] = r * n + c
                queue.append((nr, nc))

    reachable = np.argwhere(parent != -1)
    path: list[list[int]] = []
    if crossing_end is not None:
        r, c = crossing_end
        while True:
            path.append([int(r), int(c)])
            pidx = int(parent[r, c])
            if pidx == -2:
                break
            if pidx < 0:
                raise RuntimeError("BFS parent chain ended before its left root")
            r, c = divmod(pidx, n)
        path.reverse()
    return reachable.astype(int).tolist(), path


def wilson_interval(k: int, trials: int) -> tuple[float, float]:
    """Calculate the two-sided 95% Wilson score interval."""
    rate = k / trials
    z2 = Z_975 * Z_975
    denominator = 1.0 + z2 / trials
    center = (rate + z2 / (2.0 * trials)) / denominator
    half_width = (
        Z_975
        * math.sqrt(rate * (1.0 - rate) / trials + z2 / (4.0 * trials * trials))
        / denominator
    )
    lo = 0.0 if k == 0 else max(0.0, center - half_width)
    hi = 1.0 if k == trials else min(1.0, center + half_width)
    return lo, hi


def run_condition(n: int, p: float, trials: int, stream: int) -> dict[str, Any]:
    seed = derive_seed(stream, n, p)
    rng = np.random.default_rng(seed)
    k = 0
    for _ in range(trials):
        if spans_left_to_right(rng.random((n, n)) < p):
            k += 1
    rate = k / trials
    lo, hi = wilson_interval(k, trials)
    return {
        "n": n,
        "p": p,
        "k": k,
        "trials": trials,
        "rate": rate,
        "lo": lo,
        "hi": hi,
        "seed": seed,
    }


def first_crossing_threshold(u: np.ndarray) -> float:
    """Find the first opening level at which the lattice spans left to right."""
    values = np.unique(u)
    low, high = 0, len(values) - 1
    while low < high:
        middle = (low + high) // 2
        if spans_left_to_right(u <= values[middle]):
            high = middle
        else:
            low = middle + 1
    return float(values[low])


def find_example() -> tuple[int, np.ndarray, float, list[list[int]], list[list[int]]]:
    n = 24
    for seed in range(BASE_SEED, BASE_SEED + 100_000):
        u = np.random.default_rng(seed).random((n, n))
        threshold = first_crossing_threshold(u)
        if 0.58 < threshold < 0.60:
            reachable, path = bfs_reachable_and_path(u <= threshold)
            if not path:
                raise RuntimeError("Selected threshold did not yield a BFS path")
            return seed, u, threshold, reachable, path
    raise RuntimeError("Could not find a 24x24 example in the requested threshold range")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_example(
    u: np.ndarray,
    threshold: float,
    reachable_coords: list[list[int]],
    path: list[list[int]],
) -> None:
    n = u.shape[0]
    open_cells = u <= threshold
    reachable = {tuple(coord) for coord in reachable_coords}
    if not 0.58 < threshold < 0.60 or not np.any(u == threshold):
        raise RuntimeError("Example threshold is not a sampled first-opening value in (0.58, 0.60)")
    if not path or path[0][1] != 0 or path[-1][1] != n - 1:
        raise RuntimeError("Example path does not connect the left and right boundaries")
    for r, c in path:
        if not (0 <= r < n and 0 <= c < n and open_cells[r, c]):
            raise RuntimeError("Example path contains an invalid or closed cell")
    for (r1, c1), (r2, c2) in zip(path, path[1:]):
        if abs(r1 - r2) + abs(c1 - c2) != 1:
            raise RuntimeError("Example path contains a non-four-neighbor step")
    if any(tuple(coord) not in reachable for coord in path):
        raise RuntimeError("Example path is not included in the BFS reachable set")
    if not all((r, 0) in reachable for r in range(n) if open_cells[r, 0]):
        raise RuntimeError("BFS reachable set omitted an open left-edge cell")
    for r, c in reachable:
        if not (0 <= r < n and 0 <= c < n and open_cells[r, c]):
            raise RuntimeError("BFS reachable set contains an invalid or closed cell")
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < n and 0 <= nc < n and open_cells[nr, nc]:
                if (nr, nc) not in reachable:
                    raise RuntimeError("BFS reachable set is not complete")
    if spans_left_to_right(u <= np.nextafter(threshold, -math.inf)):
        raise RuntimeError("Selected threshold is not the first opening level")


def main() -> None:
    started = time.monotonic()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    comparison: list[dict[str, Any]] = []
    comparison_seeds: list[dict[str, Any]] = []
    for p in COMPARISON_P:
        result = run_condition(128, p, COMPARISON_TRIALS, stream=2)
        comparison.append(result)
        comparison_seeds.append({"n": 128, "p": p, "seed": result["seed"]})
        print(f"完成比较 N=128 p={p:.2f}: k={result['k']}/{result['trials']}", flush=True)

    comparison_public = [
        {key: result[key] for key in ("n", "p", "k", "trials", "rate", "lo", "hi")}
        for result in comparison
    ]
    with (DATA_DIR / "comparison.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "comparison": comparison_public,
                "seeds": comparison_seeds,
                "method": "独立均匀随机场；四邻接；开放边界；左到右贯穿；Wilson 95% CI。",
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    write_csv(
        DATA_DIR / "simulation_comparison.csv",
        ["n", "p", "k", "trials", "rate", "lo", "hi", "seed"],
        comparison,
    )
    print(f"comparison.json 已写入 {DATA_DIR / 'comparison.json'}", flush=True)

    curves: list[dict[str, Any]] = []
    curve_seeds: list[dict[str, Any]] = []
    for n in SIZES:
        for p in P_VALUES:
            result = run_condition(n, p, TRIALS, stream=1)
            curves.append(result)
            curve_seeds.append({"n": n, "p": p, "seed": result["seed"]})
        print(f"完成曲线 N={n}: {len(P_VALUES)} 个 p 点", flush=True)

    example_seed, example_u, threshold, reachable, path = find_example()
    validate_example(example_u, threshold, reachable, path)
    path_set = {tuple(coord) for coord in path}
    reachable_set = {tuple(coord) for coord in reachable}

    small_n = 12
    small_seed = derive_seed(stream=3, n=small_n, p=0.40)
    small_u = np.random.default_rng(small_seed).random((small_n, small_n))

    for result in curves + comparison:
        if result["k"] < 0 or result["k"] > result["trials"]:
            raise RuntimeError("Invalid crossing count")
        if abs(result["rate"] - result["k"] / result["trials"]) > 1e-15:
            raise RuntimeError("Rate does not agree with its crossing count")
        if not result["lo"] <= result["rate"] <= result["hi"]:
            raise RuntimeError("Wilson interval does not contain the estimate")

    curve_public = [
        {key: result[key] for key in ("n", "p", "k", "trials", "rate", "lo", "hi")}
        for result in curves
    ]
    data = {
        "metadata": {
            "description": "二维正方格点独立点渗流，四邻接、开放边界、左到右贯穿。",
            "base_seed": BASE_SEED,
            "bit_generator": "NumPy Generator / PCG64",
            "seed_derivation": (
                "每个 (stream,N,p) 使用 SeedSequence([base_seed,stream,N,p_milli]) "
                "生成独立的 64-bit seed；曲线 stream=1，比较 stream=2。"
            ),
            "method": (
                "每次试验独立生成 N×N 均匀随机场并以 u<p 开放格点；"
                "优先 scipy.ndimage.label，structure 为四邻接十字模板；"
                "SciPy 缺失时使用逐层 NumPy mask 膨胀作四邻接连通性检查。"
            ),
            "scipy_version": SCIPY_VERSION,
            "fallback_used": ndimage is None,
            "confidence_interval": "Wilson score interval, two-sided 95%, z=1.959963984540054",
            "curve_seed_manifest": curve_seeds,
            "comparison_seed_manifest": comparison_seeds,
            "small_p_examples": [0.30, 0.50],
        },
        "curves": curve_public,
        "comparison": comparison_public,
        "example": {
            "n": 24,
            "seed": example_seed,
            "u": example_u.tolist(),
            "threshold": threshold,
            "reachable": reachable,
            "path": path,
            "selection": "按 seed 从 20261006 起顺序搜索，选择首个阈值严格位于 (0.58, 0.60) 的样本。",
        },
        "small": {"n": small_n, "seed": small_seed, "u": small_u.tolist()},
    }
    with (DATA_DIR / "simulation.json").open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    csv_fields = ["n", "p", "k", "trials", "rate", "lo", "hi", "seed"]
    write_csv(DATA_DIR / "simulation_curves.csv", csv_fields, curves)
    write_csv(DATA_DIR / "simulation_comparison.csv", csv_fields, comparison)

    path_rows = []
    for r in range(24):
        for c in range(24):
            path_rows.append(
                {
                    "n": 24,
                    "seed": example_seed,
                    "row": r,
                    "col": c,
                    "u": format(float(example_u[r, c]), ".17g"),
                    "open_at_threshold": int(example_u[r, c] <= threshold),
                    "bfs_reachable": int((r, c) in reachable_set),
                    "path_member": int((r, c) in path_set),
                }
            )
    write_csv(
        DATA_DIR / "example_cells.csv",
        ["n", "seed", "row", "col", "u", "open_at_threshold", "bfs_reachable", "path_member"],
        path_rows,
    )

    small_rows = []
    for r in range(small_n):
        for c in range(small_n):
            small_rows.append(
                {
                    "n": small_n,
                    "seed": small_seed,
                    "row": r,
                    "col": c,
                    "u": format(float(small_u[r, c]), ".17g"),
                    "open_at_p_0_30": int(small_u[r, c] < 0.30),
                    "open_at_p_0_50": int(small_u[r, c] < 0.50),
                }
            )
    write_csv(
        DATA_DIR / "small_example_cells.csv",
        ["n", "seed", "row", "col", "u", "open_at_p_0_30", "open_at_p_0_50"],
        small_rows,
    )

    # Re-read the published JSON to validate the requested interface shape.
    loaded = json.loads((DATA_DIR / "simulation.json").read_text(encoding="utf-8"))
    assert len(loaded["curves"]) == 3 * len(P_VALUES)
    assert len(loaded["comparison"]) == 2
    assert set(loaded["curves"][0]) == {
        "n", "p", "k", "trials", "rate", "lo", "hi"
    }
    assert all(row["n"] == 128 for row in loaded["comparison"])
    assert len(path_rows) == 24 * 24 and len(small_rows) == 12 * 12

    report = [
        "# Simulation validation report",
        "",
        "- Model: independent square-lattice site percolation; 4-neighbor connectivity; open boundaries; left-to-right crossing.",
        f"- NumPy: {np.__version__}; SciPy: {SCIPY_VERSION or 'unavailable'}.",
        f"- Connectivity implementation: {'scipy.ndimage.label' if ndimage is not None else 'fallback NumPy layer-wise mask expansion with four orthogonal neighbors'}.",
        f"- Curve rows: {len(curves)} (N=32/64/128, p=0.50..0.68 by 0.01, {TRIALS} trials each).",
        f"- Comparison rows: {len(comparison)} (N=128, p=0.58/0.60, {COMPARISON_TRIALS} trials each).",
        f"- Example: N=24, seed={example_seed}, first-crossing threshold={threshold:.17g}; reachable cells={len(reachable)}; path length={len(path)} cells.",
        f"- Example path boundary check: starts at column {path[0][1]}, ends at column {path[-1][1]} (expected 0 and 23): PASS.",
        "- Example path four-neighbor check (Manhattan distance exactly 1 for every consecutive pair): PASS.",
        "- Example path open-cell and BFS-reachable membership checks: PASS.",
        "- BFS reachable-set closure/completeness check: PASS.",
        "- Threshold minimality check (no crossing immediately below the selected opening value): PASS.",
        "- JSON interface check: curves contain 57 rows; comparison contains exactly two N=128 rows; required field sets and N=12/N=24 cell counts: PASS.",
        f"- All rates, Wilson 95% intervals, and crossing counts checked: PASS.",
        f"- Seed manifests are stored in simulation.json metadata and in both summary CSVs.",
        f"- Runtime: {time.monotonic() - started:.2f} seconds.",
        "",
        "## N=128 comparison results",
        "",
        "| p | k / trials | rate | Wilson 95% CI |",
        "|---:|---:|---:|---:|",
    ]
    report.extend(
        f"| {row['p']:.2f} | {row['k']} / {row['trials']} | {row['rate']:.6f} | [{row['lo']:.6f}, {row['hi']:.6f}] |"
        for row in comparison
    )
    report.extend(["", "## N=24 selected example path", "", "```text"])
    report.append(" -> ".join(f"({r},{c})" for r, c in path))
    report.extend(["```", ""])
    (DATA_DIR / "validation_report.md").write_text("\n".join(report), encoding="utf-8")

    print(f"N=24 example seed={example_seed}, threshold={threshold:.8f}", flush=True)
    for row in comparison:
        print(
            f"N=128 p={row['p']:.2f}: {row['k']}/{row['trials']} "
            f"rate={row['rate']:.6f} Wilson95=[{row['lo']:.6f}, {row['hi']:.6f}]",
            flush=True,
        )
    print(f"输出目录: {DATA_DIR}", flush=True)


if __name__ == "__main__":
    main()
