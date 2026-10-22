"""
03_analysis.py — AHP weighting, factor reclassification, weighted overlay.

1. Rebuild the pairwise comparison matrix (Table A1 of the source paper),
   solve the principal eigenvector, compute the Consistency Ratio and
   compare against the published weights (Table 2) as a sanity check.
2. Reclassify each factor to a 1-5 susceptibility rating:
     * continuous factors -> five classes via Jenks natural breaks computed
       on the AOI's own values (the paper likewise used natural breaks on
       its study-area ranges), oriented by FACTOR_DIRECTION;
     * LULC and aspect -> ordinal mapping following the paper's Table A2.
3. Weighted linear overlay with the published weights -> continuous
   susceptibility index, then Jenks five-class final map.

Outputs (data/processed): reclass_<factor>.tif, susceptibility_index.tif,
susceptibility_class.tif; outputs/ahp_report.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (AHP_UPPER, ASPECT_RATING_FLAT, DATA_PROCESSED, FACTORS,
                    FACTOR_DIRECTION, LULC_RATING, OUTPUTS, PUBLISHED_CR,
                    PUBLISHED_WEIGHTS)

NODATA = -9999.0
RI_10 = 1.49  # Saaty random consistency index, n=10


# ------------------------------------------------------------------- AHP ---
def ahp_solve():
    n = len(FACTORS)
    M = np.zeros((n, n))
    for i, f in enumerate(FACTORS):
        row = AHP_UPPER[f]
        for k, v in enumerate(row):
            j = i + k
            M[i, j] = v
            M[j, i] = 1.0 / v
    eigvals, eigvecs = np.linalg.eig(M)
    imax = int(np.argmax(eigvals.real))
    lam = eigvals.real[imax]
    w = np.abs(eigvecs[:, imax].real)
    w = w / w.sum()
    ci = (lam - n) / (n - 1)
    cr = ci / RI_10
    solved = dict(zip(FACTORS, np.round(w, 3)))
    print("AHP eigenvector solve:")
    print(f"  lambda_max={lam:.3f}  CI={ci:.3f}  CR={cr:.3f} "
          f"(paper reports {PUBLISHED_CR})")
    for f in FACTORS:
        d = solved[f] - PUBLISHED_WEIGHTS[f]
        print(f"  {f:18s} solved={solved[f]:.3f}  published="
              f"{PUBLISHED_WEIGHTS[f]:.3f}  d={d:+.3f}")
    if cr >= 0.10:
        print("  WARNING: CR >= 0.10")
    return solved, float(lam), float(ci), float(cr)


# ---------------------------------------------------------------- rasters --
def load(name):
    with rasterio.open(DATA_PROCESSED / f"{name}.tif") as ds:
        arr = ds.read(1).astype("float64")
        nd = ds.nodata
        profile = ds.profile
    mask = (arr == nd) | np.isnan(arr) if nd is not None else np.isnan(arr)
    return np.where(mask, np.nan, arr), profile


def save(name, arr, profile, dtype="float32", nodata=NODATA):
    prof = dict(profile, dtype=dtype, nodata=nodata, compress="deflate")
    with rasterio.open(DATA_PROCESSED / f"{name}.tif", "w", **prof) as dst:
        dst.write(np.where(np.isnan(arr), nodata, arr).astype(dtype), 1)


def jenks_breaks(values, k=5, sample=50000, seed=42):
    import jenkspy
    v = values[np.isfinite(values)]
    rng = np.random.default_rng(seed)
    if v.size > sample:
        v = rng.choice(v, sample, replace=False)
    return jenkspy.jenks_breaks(v, n_classes=k)


def classify(arr, breaks):
    """values -> classes 1..5 given 6 break points (ascending)."""
    out = np.full(arr.shape, np.nan)
    for c in range(1, 6):
        lo, hi = breaks[c - 1], breaks[c]
        sel = (arr >= lo) & (arr <= hi) if c == 5 else (arr >= lo) & (arr < hi)
        out[sel] = c
    out[np.isnan(arr)] = np.nan
    # clamp anything outside the sampled break range
    out[(arr < breaks[0]) & np.isfinite(arr)] = 1
    out[(arr > breaks[-1]) & np.isfinite(arr)] = 5
    return out


def reclass_factor(name, arr):
    if name == "lulc":
        out = np.full(arr.shape, np.nan)
        for code, rating in LULC_RATING.items():
            out[arr == code] = rating
        return out, {"scheme": "WorldCover ordinal mapping",
                     "mapping": LULC_RATING}
    if name == "aspect":
        out = np.full(arr.shape, np.nan)
        a = arr
        out[(a >= 337.5) | ((a >= 0) & (a < 22.5))] = 5          # N
        out[((a >= 22.5) & (a < 67.5)) | ((a >= 292.5) & (a < 337.5))] = 4  # NE/NW
        out[((a >= 67.5) & (a < 112.5)) | ((a >= 247.5) & (a < 292.5))] = 3  # E/W
        out[((a >= 112.5) & (a < 157.5)) | ((a >= 202.5) & (a < 247.5))] = 2  # SE/SW
        out[(a >= 157.5) & (a < 202.5)] = 1                       # S
        out[a == -1] = ASPECT_RATING_FLAT                         # flats
        return out, {"scheme": "paper Table A2 ordinal (flat=3)"}
    breaks = jenks_breaks(arr)
    rating = classify(arr, breaks)
    if FACTOR_DIRECTION[name] < 0:
        rating = 6 - rating
    return rating, {"scheme": "jenks5", "breaks": [round(float(b), 5) for b in breaks],
                    "direction": FACTOR_DIRECTION[name]}


def main():
    solved, lam, ci, cr = ahp_solve()

    profile = None
    ratings, report = {}, {}
    print("\nReclassification (1-5):")
    for f in FACTORS:
        arr, profile = load(f)
        r, meta = reclass_factor(f, arr)
        ratings[f] = r
        report[f] = meta
        save(f"reclass_{f}", r, profile)
        vals, counts = np.unique(r[np.isfinite(r)], return_counts=True)
        dist = {int(v): round(float(c) / counts.sum(), 3)
                for v, c in zip(vals, counts)}
        print(f"  {f:18s} class share {dist}")

    # ------------------------------------------------- weighted overlay ----
    print("\nWeighted overlay (published weights)...")
    idx = np.zeros_like(ratings[FACTORS[0]])
    wsum = np.zeros_like(idx)
    for f in FACTORS:
        w = PUBLISHED_WEIGHTS[f]
        r = ratings[f]
        valid = np.isfinite(r)
        idx = np.where(valid, idx + w * np.nan_to_num(r), idx)
        wsum = np.where(valid, wsum + w, wsum)
    # normalise where some factors are nodata; mask where most are missing
    with np.errstate(invalid="ignore", divide="ignore"):
        index = np.where(wsum > 0.5, idx / wsum, np.nan)

    # mask permanent water bodies (sea, Albufera lagoon): they are water,
    # not flood-susceptible land (deviation from source paper, see README)
    lulc_raw, _ = load("lulc")
    index = np.where(lulc_raw == 80, np.nan, index)
    save("susceptibility_index", index, profile)

    breaks = jenks_breaks(index)
    classes = classify(index, breaks)
    save("susceptibility_class", classes, profile)
    vals, counts = np.unique(classes[np.isfinite(classes)], return_counts=True)
    share = {int(v): round(float(c) / counts.sum(), 3) for v, c in zip(vals, counts)}
    print(f"  index range [{np.nanmin(index):.2f}, {np.nanmax(index):.2f}]")
    print(f"  final class shares: {share}")

    OUTPUTS.mkdir(exist_ok=True)
    (OUTPUTS / "ahp_report.json").write_text(json.dumps({
        "solved_weights": solved,
        "published_weights": PUBLISHED_WEIGHTS,
        "lambda_max": round(lam, 4), "CI": round(ci, 4), "CR": round(cr, 4),
        "published_CR": PUBLISHED_CR,
        "reclassification": report,
        "index_breaks": [round(float(b), 4) for b in breaks],
        "class_shares": share,
    }, indent=2))
    print("\nWrote outputs/ahp_report.json")


if __name__ == "__main__":
    main()
