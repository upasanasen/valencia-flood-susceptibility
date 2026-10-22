"""
01_fetch_data.py — download every input layer for the AOI.

All sources are open and fetched programmatically:
  * DEM ............ Copernicus DEM GLO-30 (AWS Open Data, no auth)
  * Land cover ..... ESA WorldCover 2021 v200 (AWS, no auth)
  * Streams/roads .. OpenStreetMap via Overpass API
  * Sentinel-2 ..... L2A COGs via Element84 Earth Search STAC (AWS, no auth)
  * Validation ..... Copernicus EMS EMSR773 delineation vectors

MDT05 note: for the 5 m Spanish national DTM, download the MDT05 sheets
covering the AOI manually from the CNIG download centre
(https://centrodedescargas.cnig.es/) into data/raw/mdt05/ and set
RESOLUTION = 5 in config.py. 02_preprocess.py will mosaic any *.tif/*.asc
found there in preference to GLO-30.

Everything lands in data/raw/. Re-runs skip files that already exist.
"""
import json
import math
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (AOI, DATA_RAW, EMS_DASHBOARD_API, EMS_PRODUCT_MONIT,
                    S2_COLLECTION, S2_DATE_RANGE, S2_MAX_CLOUD, STAC_URL)

UA = {"User-Agent": "valencia-flood-susceptibility/1.0 (research pipeline)"}


def _remote_size(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length", 0))
    except Exception:
        return 0


def _get(url, dest, desc=""):
    """Download with skip-if-complete and HTTP Range resume."""
    dest = Path(dest)
    have = dest.stat().st_size if dest.exists() else 0
    want = _remote_size(url)
    if have > 0 and (want == 0 or have == want):
        print(f"  [skip] {dest.name} already present")
        return dest
    mode, headers = "wb", dict(UA)
    if 0 < have < want:
        mode, headers["Range"] = "ab", f"bytes={have}-"
        print(f"  [res ] {desc or dest.name} from {have/1e6:.1f} MB")
    else:
        print(f"  [get ] {desc or dest.name}\n         {url}")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r, open(dest, mode) as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print(f"         -> {dest.stat().st_size/1e6:.1f} MB")
    return dest


# ------------------------------------------------------------------ DEM ----
def fetch_dem():
    print("== Copernicus DEM GLO-30")
    lon_min, lat_min, lon_max, lat_max = AOI
    tiles = set()
    for lat in range(math.floor(lat_min), math.ceil(lat_max)):
        for lon in range(math.floor(lon_min), math.ceil(lon_max)):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            tiles.add(f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM")
    out = DATA_RAW / "dem"
    out.mkdir(exist_ok=True)
    for t in sorted(tiles):
        url = f"https://copernicus-dem-30m.s3.amazonaws.com/{t}/{t}.tif"
        _get(url, out / f"{t}.tif")


# ----------------------------------------------------------- land cover ----
def fetch_worldcover():
    print("== ESA WorldCover 2021 v200")
    lon_min, lat_min, lon_max, lat_max = AOI
    tiles = set()
    for lat in range(math.floor(lat_min / 3) * 3, math.ceil(lat_max / 3) * 3, 3):
        for lon in range(math.floor(lon_min / 3) * 3, math.ceil(lon_max / 3) * 3, 3):
            ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
            ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
            tiles.add(f"ESA_WorldCover_10m_2021_v200_{ns}{ew}_Map")
    out = DATA_RAW / "worldcover"
    out.mkdir(exist_ok=True)
    for t in sorted(tiles):
        url = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/"
               f"v200/2021/map/{t}.tif")
        _get(url, out / f"{t}.tif")


# ------------------------------------------------------------------ OSM ----
OVERPASS = "https://overpass-api.de/api/interpreter"

def _overpass(query, dest, desc):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [skip] {dest.name} already present")
        return
    print(f"  [get ] {desc} via Overpass")
    data = urllib.request.urlopen(
        urllib.request.Request(OVERPASS, data=query.encode(), headers=UA),
        timeout=600).read()
    dest.write_bytes(data)
    print(f"         -> {len(data)/1e6:.1f} MB")


def fetch_osm():
    print("== OpenStreetMap (Overpass)")
    lon_min, lat_min, lon_max, lat_max = AOI
    bbox = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    streams = f"""[out:json][timeout:600];
(way["waterway"~"^(river|stream|canal|drain|ditch)$"]({bbox}););
out geom;"""
    roads = f"""[out:json][timeout:600];
(way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential)$"]({bbox}););
out geom;"""
    _overpass(streams, DATA_RAW / "osm_streams.json", "waterways")
    _overpass(roads, DATA_RAW / "osm_roads.json", "roads")


# ------------------------------------------------------------ Sentinel-2 ---
def fetch_s2():
    """Find the least-cloudy pre-event Sentinel-2 L2A scene covering the AOI
    centre and save the STAC item (bands are streamed as COGs later)."""
    print("== Sentinel-2 L2A scene selection (STAC)")
    dest = DATA_RAW / "s2_item.json"
    if dest.exists():
        print("  [skip] s2_item.json already present")
        return
    from pystac_client import Client
    cat = Client.open(STAC_URL)
    lon_min, lat_min, lon_max, lat_max = AOI
    search = cat.search(
        collections=[S2_COLLECTION],
        bbox=AOI,
        datetime=S2_DATE_RANGE,
        query={"eo:cloud_cover": {"lt": S2_MAX_CLOUD}},
        max_items=100,
    )
    items = list(search.items())
    if not items:
        raise SystemExit("No Sentinel-2 scenes found — relax S2_MAX_CLOUD "
                         "or widen S2_DATE_RANGE in config.py")
    # prefer scenes whose footprint covers the AOI centre, then least cloud
    from shapely.geometry import Point, shape
    centre = Point((lon_min + lon_max) / 2, (lat_min + lat_max) / 2)
    covering = [i for i in items if shape(i.geometry).contains(centre)]
    pool = covering or items
    best = min(pool, key=lambda i: i.properties["eo:cloud_cover"])
    print(f"  [sel ] {best.id}  cloud={best.properties['eo:cloud_cover']:.1f}%"
          f"  date={best.datetime.date()}")
    dest.write_text(json.dumps(best.to_dict()))


# ----------------------------------------------------- EMS validation ------
def fetch_ems():
    print("== Copernicus EMS EMSR773 delineation")
    zdest = DATA_RAW / "emsr773_del.zip"
    if not zdest.exists() or zdest.stat().st_size == 0:
        meta = json.loads(urllib.request.urlopen(
            urllib.request.Request(EMS_DASHBOARD_API, headers=UA),
            timeout=120).read())
        act = meta["results"][0] if "results" in meta else meta
        prods = act["aois"][0]["products"]
        prod = next(p for p in prods
                    if p["type"] == "DEL"
                    and p.get("monitoringNumber", 0) == EMS_PRODUCT_MONIT)
        _get(prod["downloadPath"], zdest, "EMSR773 DEL product")
    out = DATA_RAW / "emsr773"
    if not out.exists():
        out.mkdir()
        with zipfile.ZipFile(zdest) as z:
            names = [n for n in z.namelist() if "observedEvent" in n]
            z.extractall(out, members=names)
        print(f"  [unzip] {len(names)} observedEvent files -> {out}")


if __name__ == "__main__":
    fetch_dem()
    fetch_worldcover()
    fetch_osm()
    fetch_s2()
    fetch_ems()
    print("\nAll raw data present in", DATA_RAW)
