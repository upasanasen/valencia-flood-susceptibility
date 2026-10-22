"""
04_export_outputs.py — validation against EMSR773 and final figures/tables.

Outputs (outputs/):
  susceptibility_map.png   five-class map with observed flood outline
  roc_curve.png            ROC of the continuous index vs observed flood
  class_area_stats.csv     area and share per susceptibility class
  validation_report.json   AUC, per-class flood capture, headline numbers
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import AOI_NAME, DATA_PROCESSED, OUTPUTS, RESOLUTION

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

CLASS_NAMES = {1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High"}
CLASS_COLORS = ["#1a9641", "#a6d96a", "#ffffbf", "#fdae61", "#d7191c"]


def load(name):
    with rasterio.open(DATA_PROCESSED / f"{name}.tif") as ds:
        arr = ds.read(1).astype("float64")
        nd = ds.nodata
        extent = rasterio.plot.plotting_extent(ds)
    mask = (arr == nd) if nd is not None else np.isnan(arr)
    return np.where(mask, np.nan, arr), extent


def main():
    classes, extent = load("susceptibility_class")
    index, _ = load("susceptibility_index")
    flood, _ = load("flood_observed")
    flood = np.nan_to_num(flood, nan=0).astype(bool)
    valid = np.isfinite(classes) & np.isfinite(index)

    # ------------------------------------------------------------- ROC ----
    from sklearn.metrics import roc_auc_score, roc_curve
    y = flood[valid].astype(int)
    score = index[valid]
    auc = roc_auc_score(y, score)
    fpr, tpr, _ = roc_curve(y, score)
    print(f"ROC AUC (index vs EMSR773 observed flood): {auc:.3f}")

    # ------------------------------------------- per-class flood capture ---
    rows, capture = [], {}
    cell_km2 = (RESOLUTION ** 2) / 1e6
    for c in range(1, 6):
        sel = valid & (classes == c)
        n = int(sel.sum())
        n_fl = int((sel & flood).sum())
        rows.append({
            "class": c, "name": CLASS_NAMES[c],
            "area_km2": round(n * cell_km2, 2),
            "share_of_aoi": round(n / valid.sum(), 4),
            "flooded_km2": round(n_fl * cell_km2, 2),
            "share_of_flood": round(n_fl / max(flood[valid].sum(), 1), 4),
            "flood_rate_in_class": round(n_fl / max(n, 1), 4),
        })
        capture[CLASS_NAMES[c]] = rows[-1]["share_of_flood"]
    high_capture = capture["High"] + capture["Very High"]
    print(f"Observed flood falling in High+Very High classes: "
          f"{100 * high_capture:.1f}%")

    with open(OUTPUTS / "class_area_stats.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    (OUTPUTS / "validation_report.json").write_text(json.dumps({
        "aoi": AOI_NAME,
        "resolution_m": RESOLUTION,
        "roc_auc": round(float(auc), 4),
        "flood_capture_high_veryhigh": round(float(high_capture), 4),
        "per_class": rows,
        "reference": "Copernicus EMS EMSR773 DEL observed event (2 Nov 2024)",
    }, indent=2))

    # ------------------------------------------------------------- maps ----
    fig, ax = plt.subplots(figsize=(9, 10), dpi=150)
    cmap = ListedColormap(CLASS_COLORS)
    shown = np.where(np.isfinite(classes), classes, np.nan)
    ax.imshow(shown, cmap=cmap, vmin=0.5, vmax=5.5, extent=extent,
              interpolation="nearest")
    fl = np.where(flood & valid, 1.0, np.nan)
    ax.imshow(fl, cmap=ListedColormap(["#08306b"]), extent=extent,
              interpolation="nearest", alpha=0.35)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in CLASS_COLORS]
    handles.append(plt.Rectangle((0, 0), 1, 1, color="#08306b", alpha=0.35))
    ax.legend(handles, [CLASS_NAMES[i] for i in range(1, 6)]
              + ["EMSR773 observed flood"], loc="lower right", fontsize=8,
              title="Susceptibility", title_fontsize=9)
    ax.set_title("Flood susceptibility — Valencia (GIS–AHP)\n"
                 f"vs. 29 Oct 2024 DANA observed extent  |  AUC={auc:.3f}",
                 fontsize=11)
    ax.set_xlabel("ETRS89 / UTM 30N — easting (m)", fontsize=8)
    ax.set_ylabel("northing (m)", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(OUTPUTS / "susceptibility_map.png", bbox_inches="tight")
    print("Wrote outputs/susceptibility_map.png")

    fig2, ax2 = plt.subplots(figsize=(5, 5), dpi=150)
    ax2.plot(fpr, tpr, color="#d7191c", lw=1.5, label=f"AUC = {auc:.3f}")
    ax2.plot([0, 1], [0, 1], "k--", lw=0.8, label="random")
    ax2.set_xlabel("False positive rate")
    ax2.set_ylabel("True positive rate")
    ax2.set_title("ROC — susceptibility index vs EMSR773 observed flood")
    ax2.legend()
    fig2.tight_layout()
    fig2.savefig(OUTPUTS / "roc_curve.png", bbox_inches="tight")
    print("Wrote outputs/roc_curve.png")


if __name__ == "__main__":
    import rasterio.plot  # noqa: F401  (needed for plotting_extent)
    main()
