# QGIS project assembly

The pipeline produces GeoTIFFs; the presentation project is assembled in
QGIS desktop (any 3.34+ LTR). The `.qgz` is not committed with absolute
paths baked in — build it locally in two minutes:

1. Run the pipeline (`scripts/01…04`), so `data/processed/` is populated.
2. New QGIS project → set project CRS to **EPSG:25830**.
3. Add layers, bottom to top:
   - `data/processed/dem.tif` → right-click ▸ Properties ▸ Symbology ▸
     *Hillshade* (Z factor 1.5), ~40 % opacity — background relief.
   - `data/processed/susceptibility_class.tif` → *Paletted/Unique values*:
     `1 #1a9641 Very Low · 2 #a6d96a Low · 3 #ffffbf Moderate ·
      4 #fdae61 High · 5 #d7191c Very High`, ~75 % opacity.
   - `data/raw/emsr773/…observedEventA_v1.shp` → no fill, dark-blue outline
     0.4 mm (observed 2024 DANA flood extent).
4. Project ▸ Layouts ▸ *Manage Layouts* ▸ import
   `layouts/flood_susceptibility_a4.qpt`, assign the map item to the canvas,
   refresh legend.
5. Save as `qgis/valencia_flood_susceptibility.qgz` (use *relative paths*,
   the default, so the project stays portable).

Export the layout as PNG (300 dpi) into `outputs/` if you update the map
shown in the README.
