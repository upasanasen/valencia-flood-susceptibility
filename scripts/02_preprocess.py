"""
02_preprocess.py — build the ten conditioning-factor rasters on a common grid.

Everything is reprojected/clipped/resampled to one grid: AOI bounds in
EPSG:25830 at RESOLUTION metres (config.py). Outputs to data/processed/:

  dem.tif, slope.tif, aspect.tif, twi.tif, drainage_density.tif,
  dist_streams.tif, dist_roads.tif, lulc.tif, ndvi.tif, ndbi.tif
  flood_observed.tif   (EMSR773 rasterised — validation only, NOT a factor)

Method notes
  * Slope/aspect: Horn (1981) 3x3, as in GDAL/QGIS.
  * TWI = ln(SCA / tan(slope)) with SCA from pysheds D8 flow accumulation
    on the depression-filled DEM.
  * Drainage density: stream length per unit area, approximated as
    (stream-cell count x cell size) / window area over a 1 km circular
    moving window.
  * Distances: Euclidean distance transform on rasterised OSM lines.
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import AOI, CRS, DATA_PROCESSED, DATA_RAW, RESOLUTION

NODATA = -9999.0


# ------------------------------------------------------------ target grid --
def target_grid():
    l, b, r, t = transform_bounds("EPSG:4326", CRS, *AOI)
    l, b = np.floor(l / RESOLUTION) * RESOLUTION, np.floor(b / RESOLUTION) * RESOLUTION
    r, t = np.ceil(r / RESOLUTION) * RESOLUTION, np.ceil(t / RESOLUTION) * RESOLUTION
    width, height = int((r - l) / RESOLUTION), int((t - b) / RESOLUTION)
    transform = from_origin(l, t, RESOLUTION, RESOLUTION)
    return transform, width, height


TRANSFORM, WIDTH, HEIGHT = target_grid()
PROFILE = dict(driver="GTiff", crs=CRS, transform=TRANSFORM, width=WIDTH,
               height=HEIGHT, count=1, dtype="float32", nodata=NODATA,
               compress="deflate")


def save(name, arr, dtype="float32", nodata=NODATA):
    prof = dict(PROFILE, dtype=dtype, nodata=nodata)
    with rasterio.open(DATA_PROCESSED / f"{name}.tif", "w", **prof) as dst:
        dst.write(arr.astype(dtype), 1)
    print(f"  [out ] {name}.tif  {arr.shape}  "
          f"min={np.nanmin(np.where(arr==nodata, np.nan, arr)):.3f} "
          f"max={np.nanmax(np.where(arr==nodata, np.nan, arr)):.3f}")


def warp_to_grid(src_path_or_ds, resampling, band=1):
    out = np.full((HEIGHT, WIDTH), NODATA, dtype="float32")
    ds = (rasterio.open(src_path_or_ds)
          if isinstance(src_path_or_ds, (str, Path)) else src_path_or_ds)
    reproject(rasterio.band(ds, band), out, dst_transform=TRANSFORM,
              dst_crs=CRS, dst_nodata=NODATA, resampling=resampling)
    return out


# ------------------------------------------------------------------- DEM ---
def build_dem():
    print("== DEM")
    mdt05 = sorted((DATA_RAW / "mdt05").glob("*.*")) if (DATA_RAW / "mdt05").exists() else []
    tiles = mdt05 or sorted((DATA_RAW / "dem").glob("*.tif"))
    src_label = "MDT05" if mdt05 else "Copernicus GLO-30"
    print(f"  [src ] {src_label}: {len(tiles)} tile(s)")
    dem = np.full((HEIGHT, WIDTH), NODATA, dtype="float32")
    for tp in tiles:
        part = warp_to_grid(tp, Resampling.bilinear)
        dem = np.where(part != NODATA, part, dem)
    save("dem", dem)
    return dem


# --------------------------------------------------------- slope / aspect --
def build_slope_aspect(dem):
    print("== Slope / Aspect (Horn)")
    z = np.where(dem == NODATA, np.nan, dem)
    zp = np.pad(z, 1, mode="edge")
    a = zp[:-2, :-2]; b_ = zp[:-2, 1:-1]; c = zp[:-2, 2:]
    d = zp[1:-1, :-2];                    f = zp[1:-1, 2:]
    g = zp[2:, :-2]; h = zp[2:, 1:-1];    i = zp[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * RESOLUTION)
    dzdy = ((g + 2 * h + i) - (a + 2 * b_ + c)) / (8 * RESOLUTION)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.degrees(np.arctan2(dzdy, -dzdx))
    aspect = np.mod(90.0 - aspect, 360.0)          # 0=N, clockwise
    flat = np.hypot(dzdx, dzdy) < 1e-6
    aspect[flat] = -1                              # flag flats
    save("slope", np.where(np.isnan(slope), NODATA, slope))
    save("aspect", np.where(np.isnan(aspect), NODATA, aspect))
    return slope


# ------------------------------------------------------------------- TWI ---
def build_twi(slope_deg):
    print("== TWI (pysheds D8)")
    from pysheds.grid import Grid
    dem_path = str(DATA_PROCESSED / "dem.tif")
    grid = Grid.from_raster(dem_path)
    dem = grid.read_raster(dem_path)
    filled = grid.fill_pits(dem)
    flooded = grid.fill_depressions(filled)
    inflated = grid.resolve_flats(flooded)
    fdir = grid.flowdir(inflated)
    acc = grid.accumulation(fdir)
    sca = (np.asarray(acc) + 1.0) * RESOLUTION      # specific catchment area
    tan_b = np.tan(np.radians(np.maximum(np.nan_to_num(slope_deg, nan=0.0),
                                         0.057)))   # >= 0.057 deg ~ 0.001
    twi = np.log(sca / tan_b)
    twi = np.where(np.isnan(slope_deg), NODATA, twi)
    save("twi", twi)


# ------------------------------------------------------------- OSM lines ---
def _osm_lines(path):
    """Overpass JSON -> list of shapely LineStrings in target CRS."""
    from pyproj import Transformer
    from shapely.geometry import LineString
    from shapely.ops import transform as shp_transform
    tr = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    lines = []
    for el in json.loads(Path(path).read_text())["elements"]:
        if el.get("type") == "way" and "geometry" in el and len(el["geometry"]) > 1:
            ls = LineString([(p["lon"], p["lat"]) for p in el["geometry"]])
            lines.append(shp_transform(tr.transform, ls))
    return lines


def _rasterize_lines(lines):
    return features.rasterize(((g, 1) for g in lines), out_shape=(HEIGHT, WIDTH),
                              transform=TRANSFORM, fill=0, dtype="uint8",
                              all_touched=True)


def build_hydro_env():
    print("== Streams: distance + drainage density; Roads: distance")
    streams = _rasterize_lines(_osm_lines(DATA_RAW / "osm_streams.json"))
    roads = _rasterize_lines(_osm_lines(DATA_RAW / "osm_roads.json"))
    print(f"  [info] stream cells={int(streams.sum())}, road cells={int(roads.sum())}")

    for name, mask in (("dist_streams", streams), ("dist_roads", roads)):
        dist = ndimage.distance_transform_edt(1 - mask) * RESOLUTION
        save(name, dist)

    # drainage density in a 1 km circular window (m of channel per m^2)
    radius_px = max(int(1000 / RESOLUTION), 1)
    yy, xx = np.ogrid[-radius_px:radius_px + 1, -radius_px:radius_px + 1]
    kernel = (xx ** 2 + yy ** 2 <= radius_px ** 2).astype("float32")
    stream_len = ndimage.convolve(streams.astype("float32"), kernel,
                                  mode="constant") * RESOLUTION
    area = kernel.sum() * RESOLUTION ** 2
    save("drainage_density", stream_len / area)


# ------------------------------------------------------------------ LULC ---
def build_lulc():
    print("== Land cover (ESA WorldCover)")
    tiles = sorted((DATA_RAW / "worldcover").glob("*.tif"))
    lulc = np.zeros((HEIGHT, WIDTH), dtype="float32")
    for tp in tiles:
        part = warp_to_grid(tp, Resampling.nearest)
        lulc = np.where(part != NODATA, part, lulc)
    save("lulc", lulc, dtype="int16", nodata=0)


# ------------------------------------------------------- Sentinel-2 NDVI ---
def build_spectral():
    print("== NDVI / NDBI from Sentinel-2 COGs")
    item = json.loads((DATA_RAW / "s2_item.json").read_text())
    assets = item["assets"]
    bands = {}
    for key, asset_name in (("red", "red"), ("nir", "nir"), ("swir16", "swir16")):
        url = assets[asset_name]["href"]
        print(f"  [read] {asset_name}: {url.split('/')[-1]}")
        with rasterio.open(url) as ds:
            bands[key] = warp_to_grid(ds, Resampling.bilinear)
    red, nir, swir = (np.where(bands[k] == NODATA, np.nan, bands[k])
                      for k in ("red", "nir", "swir16"))
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / (nir + red)
        ndbi = (swir - nir) / (swir + nir)
    save("ndvi", np.where(np.isnan(ndvi), NODATA, ndvi))
    save("ndbi", np.where(np.isnan(ndbi), NODATA, ndbi))


# ------------------------------------------------ EMSR773 observed flood ---
def build_flood_truth():
    print("== EMSR773 observed flood extent (validation layer)")
    import geopandas as gpd
    shp = next((DATA_RAW / "emsr773").glob("*observedEventA*.shp"))
    gdf = gpd.read_file(shp).to_crs(CRS)
    # keep flooded-area polygons (the layer also contains e.g. mud objects;
    # keep everything classed as flooded/water surface)
    flood = features.rasterize(((geom, 1) for geom in gdf.geometry),
                               out_shape=(HEIGHT, WIDTH), transform=TRANSFORM,
                               fill=0, dtype="uint8", all_touched=True)
    save("flood_observed", flood.astype("float32"), dtype="uint8", nodata=255)
    print(f"  [info] flooded cells: {int(flood.sum())} "
          f"({100*flood.mean():.1f}% of grid)")


if __name__ == "__main__":
    print(f"Grid: {WIDTH} x {HEIGHT} @ {RESOLUTION} m ({CRS})")
    dem = build_dem()
    slope = build_slope_aspect(dem)
    build_twi(slope)
    build_hydro_env()
    build_lulc()
    build_spectral()
    build_flood_truth()
    print("\nAll factor rasters in", DATA_PROCESSED)
