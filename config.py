"""
Central configuration for the Valencia flood susceptibility pipeline.

All scripts import from this file. Edit AOI / RESOLUTION here, nothing else
needs to change.
"""
from pathlib import Path

# ---------------------------------------------------------------- paths ----
ROOT = Path(__file__).resolve().parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
OUTPUTS = ROOT / "outputs"

for _d in (DATA_RAW, DATA_PROCESSED, OUTPUTS):
    _d.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ AOI ----
# Bounding boxes are (lon_min, lat_min, lon_max, lat_max) in EPSG:4326.
#
# TEST_AOI : small area over the Horta Sud comarca / lower Poyo ravine,
#            the epicentre of the 29 Oct 2024 DANA flooding. Used to verify
#            the pipeline end-to-end quickly.
# FULL_AOI : envelope of the Copernicus EMSR773 AOI-01 polygon
#            ("Valencia province") plus a small buffer, so the upstream
#            contributing area of the Poyo/Turia/Magro systems is included.
TEST_AOI = (-0.58, 39.30, -0.30, 39.55)
FULL_AOI = (-1.55, 38.68, 0.07, 40.04)

AOI = TEST_AOI          # switch to FULL_AOI for the full run
AOI_NAME = "test"       # used in filenames; set to "full" for the full run

# ----------------------------------------------------------------- grid ----
CRS = "EPSG:25830"      # ETRS89 / UTM zone 30N
RESOLUTION = 30         # metres. 30 for Copernicus GLO-30 DEM.
                        # Set 5 if you substitute the IGN/CNIG MDT05 DTM
                        # (see scripts/01_fetch_data.py, MDT05 notes).

# --------------------------------------------------------------- imagery ---
# Pre-event Sentinel-2 L2A search window (before the 29 Oct 2024 DANA).
S2_DATE_RANGE = "2024-08-15/2024-10-25"
S2_MAX_CLOUD = 20       # percent
STAC_URL = "https://earth-search.aws.element84.com/v1"
S2_COLLECTION = "sentinel-2-l2a"

# ------------------------------------------------------------ validation ---
EMS_ACTIVATION = "EMSR773"
EMS_DASHBOARD_API = (
    "https://mapping.emergency.copernicus.eu/backend/dashboard-api/"
    "public-activations/?code=" + EMS_ACTIVATION
)
# Which delineation product to validate against. DEL_PRODUCT (MONIT0) is the
# first full delineation (2 Nov 2024, close to peak flood extent).
EMS_PRODUCT_MONIT = 0

# ------------------------------------------------------------------ AHP ----
# Factor order used EVERYWHERE in the pipeline.
FACTORS = [
    "twi",              # Topographic Wetness Index
    "slope",            # Slope (degrees)
    "lulc",             # Land use / land cover (ESA WorldCover 2021)
    "drainage_density", # Stream length per unit area
    "ndvi",             # Sentinel-2 (B08-B04)/(B08+B04)
    "dist_streams",     # Euclidean distance from streams (m)
    "ndbi",             # Sentinel-2 (B11-B08)/(B11+B08)
    "dist_roads",       # Euclidean distance from roads (m)
    "dem",              # Elevation (m)
    "aspect",           # Slope direction
]

# Final pairwise comparison matrix, Table A1 of the source paper
# (Land 2026, 15(6), 1089; CC BY). Upper triangle as published, lower
# triangle = reciprocals (filled in scripts/03_analysis.py).
AHP_UPPER = {
    "twi":              [1, 2, 4, 4, 3, 3, 5, 5, 5, 7],
    "slope":            [   1, 2, 1, 3, 3, 5, 5, 4, 7],
    "lulc":             [      1, 1, 3, 2, 4, 5, 4, 4],
    "drainage_density": [         1, 3, 2, 4, 4, 5, 7],
    "ndvi":             [            1, 1, 3, 2, 3, 3],
    "dist_streams":     [               1, 4, 4, 3, 5],
    "ndbi":             [                  1, 3, 4, 5],
    "dist_roads":       [                     1, 2, 2],
    "dem":              [                        1, 4],
    "aspect":           [                           1],
}

# Published final weights, Table 2 of the source paper. Used as the weights
# of record (transferability test) and as a check on our eigenvector solve.
PUBLISHED_WEIGHTS = {
    "twi": 0.259,
    "slope": 0.169,
    "drainage_density": 0.140,
    "lulc": 0.125,
    "dist_streams": 0.089,
    "ndvi": 0.071,
    "ndbi": 0.056,
    "dist_roads": 0.035,
    "dem": 0.034,
    "aspect": 0.020,
}
PUBLISHED_CR = 0.098    # Consistency Ratio reported by the paper

# Direction of susceptibility for continuous factors:
#   +1 : higher value  -> more susceptible (rating 5)
#   -1 : higher value  -> less susceptible (rating 1)
FACTOR_DIRECTION = {
    "twi": +1,
    "slope": -1,
    "drainage_density": +1,
    "ndvi": -1,
    "dist_streams": -1,
    "ndbi": +1,
    "dist_roads": -1,
    "dem": -1,
}

# ESA WorldCover class -> susceptibility rating 1-5, adapted from the
# paper's LULC scheme (Water 5, Industrial 4, Built-up 3, Arable 2,
# Vegetation 1). WorldCover cannot separate industrial from residential
# built-up, so all built-up is rated 4 (deviation, see README).
LULC_RATING = {
    80: 5,   # permanent water bodies
    90: 5,   # herbaceous wetland
    95: 5,   # mangroves (absent here)
    50: 4,   # built-up
    60: 3,   # bare / sparse vegetation
    40: 2,   # cropland
    10: 1,   # tree cover
    20: 1,   # shrubland
    30: 1,   # grassland
    100: 1,  # moss and lichen
}

# Aspect (degrees) -> rating, paper Table A2: N 5, NE/NW 4, E/W 3,
# SE/SW 2, S 1. Flat cells (slope ~ 0) get 3 (not specified in paper).
ASPECT_RATING_FLAT = 3
