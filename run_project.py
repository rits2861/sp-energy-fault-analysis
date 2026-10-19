"""
SP Energy Networks - Fault Restoration Analysis
One script does everything: download today's data, combine, clean, analyse, map, cluster, write README.

Usage (run once a day for about a week, then a final time):
    python run_project.py "PASTE THE DATASET PAGE URL HERE"
After the first time the URL is remembered, so you can just run:
    python run_project.py
"""
import re, sys, glob, json, datetime as dt
from pathlib import Path
from urllib.request import urlopen, Request
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import folium
from sklearn.cluster import KMeans

BASE = Path(__file__).resolve().parent
RAW, CLEAN, OUT = BASE / "data" / "raw", BASE / "data" / "clean", BASE / "outputs"
for p in (RAW, CLEAN, OUT):
    p.mkdir(parents=True, exist_ok=True)
CONFIG = BASE / "project_config.json"

MIN_RESTORATION_MIN = 1
MIN_N = 3
N_CLUSTERS = 6
UK_LAT, UK_LON = (49.0, 61.0), (-9.0, 2.5)
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False, "axes.spines.right": False})

# ---------------------------------------------------------------- 1. download
def get_dataset_url():
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
        CONFIG.write_text(json.dumps({"dataset_url": url}))
        return url
    if CONFIG.exists():
        return json.loads(CONFIG.read_text()).get("dataset_url")
    return None

def download(url):
    m = re.search(r"/(?:dataset|datasets|assets)/([^/?#]+)", url or "")
    if not m:
        print("No valid dataset URL given, skipping download. Using files already in data/raw.")
        return
    ds = m.group(1)
    host = re.match(r"(https?://[^/]+)", url).group(1)
    api = f"{host}/api/explore/v2.1/catalog/datasets/{ds}/exports/csv?delimiter=%3B&lang=en&timezone=UTC"
    target = RAW / f"outages_{dt.date.today():%Y-%m-%d}.csv"
    try:
        req = Request(api, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=120) as r:
            body = r.read()
        if len(body.strip().splitlines()) < 2:
            print("Automatic download returned only the header row (no records), so it was NOT saved.")
            print("Use the download button on the portal page and save the file in data/raw/ as", target.name)
        else:
            target.write_bytes(body)
            print(f"Downloaded today's export -> {target.name}")
    except Exception as e:
        print(f"Could not download automatically ({e}).")
        print("Download the CSV by hand from the dataset's Export tab, save it in data/raw/ as", target.name)

download(get_dataset_url())
DATASET_URL = get_dataset_url() or "[add the dataset link]"

# ---------------------------------------------------------------- 2. load + combine
files = sorted(glob.glob(str(RAW / "outages_*.csv")))
if not files:
    sys.exit("No files found in data/raw/. Put at least one file named outages_YYYY-MM-DD.csv there and run again.")
EXPECTED = ["Fault ID", "DNO", "Upload Date", "Licence Area", "Region", "Incident Status", "Planned", "Planned Outage Start Date",
            "Fault Reported Date", "Estimated Time of Restoration", "Date of restoration", "Voltage", "Location Latitude", "Location Longitude"]
_key = lambda n: re.sub(r"[^a-z0-9]", "", str(n).lower())
_lookup = {_key(n): n for n in EXPECTED}
_lookup.update({"latitude": "Location Latitude", "longitude": "Location Longitude", "faultid": "Fault ID",
                "dateofrestoration": "Date of restoration", "restorationdate": "Date of restoration",
                "status": "Incident Status", "dateofreportedfault": "Fault Reported Date", "faultreporteddate": "Fault Reported Date",
                "etr": "Estimated Time of Restoration", "estimatedtimeofrestoration": "Estimated Time of Restoration"})

def load(f):
    d = pd.read_csv(f, sep=None, engine="python")
    d = d.rename(columns={c: _lookup[_key(c)] for c in d.columns if _key(c) in _lookup})
    if "Location Latitude" not in d.columns:              # some exports hold one "lat, lon" column instead
        for c in d.columns:
            if _key(c).startswith("geopoint") or _key(c) in ("location", "geolocation"):
                ll = d[c].astype(str).str.split(",", expand=True)
                if ll.shape[1] == 2:
                    d["Location Latitude"] = pd.to_numeric(ll[0], errors="coerce")
                    d["Location Longitude"] = pd.to_numeric(ll[1], errors="coerce")
                    break
    if "Planned" not in d.columns:
        d["Planned"] = 0
    missing = [c for c in EXPECTED if c not in d.columns and c not in ("Planned Outage Start Date", "DNO")]
    if missing:
        print(f"\nFile {Path(f).name} is missing columns: {missing}")
        print("Columns actually found:", list(d.columns))
        sys.exit("Copy the two lines above and send them to your helper so the column names can be matched.")
    return d.assign(source_file=Path(f).name)

_frames = [load(f) for f in files]
for _f in _frames:
    print(f"{_f['source_file'].iloc[0] if len(_f) else 'an empty file'}: {len(_f)} rows")
raw = pd.concat(_frames, ignore_index=True)
if raw.empty:
    sys.exit("\nThe downloaded file has a header row but NO fault records. The live outage feed may be empty right now.\n"
             "Check: open the dataset on the portal, click the Table tab, and see whether it shows any rows.\n"
             "If it shows rows, download the CSV by hand from the Export tab, save it in data/raw/ as outages_YYYY-MM-DD.csv, and run again.\n"
             "If it is empty, try again later today or tomorrow.")
raw["Upload Date"] = pd.to_datetime(raw["Upload Date"], errors="coerce", utc=True)
df = raw.sort_values("Upload Date").drop_duplicates(subset="Fault ID", keep="last").reset_index(drop=True)

# ---------------------------------------------------------------- 3. clean
for c in ["Fault Reported Date", "Estimated Time of Restoration", "Date of restoration"]:
    df[c] = pd.to_datetime(df[c], errors="coerce", utc=True)
df["Planned"] = pd.to_numeric(df["Planned"], errors="coerce").fillna(0)
df["restoration_min"] = (df["Date of restoration"] - df["Fault Reported Date"]).dt.total_seconds() / 60
df["eta_error_min"] = (df["Date of restoration"] - df["Estimated Time of Restoration"]).dt.total_seconds() / 60

log = [("Raw rows across all exports", len(raw)), ("Unique faults (by Fault ID)", len(df))]
_st = df["Incident Status"].astype(str).str.strip().str.lower()
_done = _st.str.contains("complet|restor|clos|resolv", regex=True)
print("Status values found:", dict(_st.value_counts()))
d = df[_done] if _done.any() else df
log.append(("Status = Completed/Restored" if _done.any() else "Status filter skipped (no completed value found)", len(d)))
d = d[d["Planned"] == 0];                         log.append(("Unplanned only", len(d)))
d = d[d["restoration_min"].notna()];              log.append(("Has restoration time", len(d)))
d = d[d["restoration_min"] >= MIN_RESTORATION_MIN]; log.append((f"Restoration >= {MIN_RESTORATION_MIN} min", len(d)))
d = d[d["Location Latitude"].between(*UK_LAT) & d["Location Longitude"].between(*UK_LON)]
log.append(("Coordinates inside UK", len(d)))
clean = d.copy()
if len(clean) < 10:
    print(f"\nWARNING: only {len(clean)} usable faults. Results will be thin. Keep running this daily to collect more.\n")
if clean.empty:
    sys.exit("No usable rows after cleaning. Check the cleaning log above and your export.")
clean.to_csv(CLEAN / "faults_clean.csv", index=False)
log_df = pd.DataFrame(log, columns=["Step", "Rows remaining"])
print(log_df.to_string(index=False))

# ---------------------------------------------------------------- 4. analysis
def summary(by):
    return (clean.groupby(by)["restoration_min"].agg(faults="count", median_min="median", mean_min="mean")
            .round(1).sort_values("median_min", ascending=False))
by_v, by_l, by_r = summary("Voltage"), summary("Licence Area"), summary("Region")
by_v.to_csv(OUT / "summary_by_voltage.csv"); by_l.to_csv(OUT / "summary_by_licence_area.csv"); by_r.to_csv(OUT / "summary_by_region.csv")

fig, ax = plt.subplots(figsize=(6, 4))
ax.bar(by_v.index, by_v["median_min"], color="#1F3864")
for i, (m, n) in enumerate(zip(by_v["median_min"], by_v["faults"])):
    ax.text(i, m, f"{m:.0f} min\n(n={n})", ha="center", va="bottom", fontsize=9)
ax.set_title("Median restoration time by voltage level"); ax.set_ylabel("Minutes"); ax.set_xlabel("Voltage")
plt.tight_layout(); plt.savefig(OUT / "chart1_voltage.png"); plt.close()

r = by_r[by_r["faults"] >= MIN_N].head(10).iloc[::-1]
if len(r):
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(r.index, r["median_min"], color="#2E75B6")
    ax.set_title(f"Slowest regions to restore (median minutes, regions with {MIN_N}+ faults)"); ax.set_xlabel("Minutes")
    plt.tight_layout(); plt.savefig(OUT / "chart2_regions.png"); plt.close()

fig, ax = plt.subplots(figsize=(7, 4))
cap = clean["restoration_min"].quantile(0.95)
ax.hist(clean["restoration_min"].clip(upper=cap), bins=30, color="#1F3864")
ax.axvline(clean["restoration_min"].median(), color="orange", ls="--", label=f"Median {clean['restoration_min'].median():.0f} min")
ax.set_title("Distribution of restoration times (capped at 95th percentile)"); ax.set_xlabel("Minutes"); ax.set_ylabel("Number of faults"); ax.legend()
plt.tight_layout(); plt.savefig(OUT / "chart3_distribution.png"); plt.close()

eta = clean.dropna(subset=["eta_error_min"])
late_pct = (eta["eta_error_min"] > 0).mean() if len(eta) else float("nan")
eta_med = eta["eta_error_min"].median() if len(eta) else float("nan")

# ---------------------------------------------------------------- 5. maps + clustering
# OpenStreetMap blocks tiles when a map is opened from a local file, so use Esri's street basemap instead
TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}"
TILE_ATTR = "Tiles &copy; Esri"
def colour(m): return "green" if m < 60 else ("orange" if m < 180 else "red")
centre = [clean["Location Latitude"].mean(), clean["Location Longitude"].mean()]
m = folium.Map(location=centre, zoom_start=7, tiles=TILES, attr=TILE_ATTR)
for _, row in clean.iterrows():
    folium.CircleMarker([row["Location Latitude"], row["Location Longitude"]], radius=5, color=colour(row["restoration_min"]),
        fill=True, fill_opacity=0.7,
        popup=folium.Popup(f"<b>{row['Fault ID']}</b><br>{row['Region']}<br>{row['Voltage']} | {row['restoration_min']:.0f} min", max_width=250)).add_to(m)
m.get_root().html.add_child(folium.Element(
    '<div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;padding:8px 12px;border:1px solid #888;font-size:13px">'
    '<b>Restoration time</b><br><span style="color:green">&#9679;</span> under 60 min<br>'
    '<span style="color:orange">&#9679;</span> 60-180 min<br><span style="color:red">&#9679;</span> over 180 min</div>'))
m.save(OUT / "fault_map.html")

fig, ax = plt.subplots(figsize=(6, 7))
ax.scatter(clean["Location Longitude"], clean["Location Latitude"], c=[colour(x) for x in clean["restoration_min"]], s=18, alpha=0.75)
ax.set_title("Fault locations (green <60 min, orange 60-180, red >180)"); ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
plt.tight_layout(); plt.savefig(OUT / "fault_scatter_map.png"); plt.close()

k = max(2, min(N_CLUSTERS, len(clean) // 5))
if len(clean) >= k:
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(clean[["Location Latitude", "Location Longitude"]].to_numpy())
    clean = clean.assign(cluster=km.labels_)
    cs = (clean.groupby("cluster").agg(faults=("Fault ID", "count"), median_restoration_min=("restoration_min", "median"),
          main_region=("Region", lambda s: s.mode().iat[0]), centre_lat=("Location Latitude", "mean"),
          centre_lon=("Location Longitude", "mean")).round(2).sort_values("faults", ascending=False))
    cs.to_csv(OUT / "cluster_summary.csv")
    pal = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#46f0f0", "#f032e6", "#bcf60c"]
    cm = folium.Map(location=centre, zoom_start=7, tiles=TILES, attr=TILE_ATTR)
    for _, row in clean.iterrows():
        folium.CircleMarker([row["Location Latitude"], row["Location Longitude"]], radius=5, color=pal[int(row["cluster"]) % len(pal)],
            fill=True, fill_opacity=0.7, popup=f"Cluster {int(row['cluster'])} | {row['Region']} | {row['restoration_min']:.0f} min").add_to(cm)
    cm.save(OUT / "cluster_map.html")
    top = cs.iloc[0]
    cluster_line = (f"The largest hotspot contains {int(top['faults'])} faults, mostly in {top['main_region']}, "
                    f"with a median restoration time of {top['median_restoration_min']:.0f} minutes.")
else:
    cluster_line = "Not enough faults to cluster yet."

# ---------------------------------------------------------------- 6. auto-written README (real numbers)
f0, f1 = clean["Fault Reported Date"].min(), clean["Fault Reported Date"].max()
overall = clean["restoration_min"].median()
v_txt = ", ".join(f"{i} {row['median_min']:.0f} min (n={int(row['faults'])})" for i, row in by_v.iterrows())
rr = by_r[by_r["faults"] >= MIN_N]
region_line = (f"Among regions with at least {MIN_N} faults, {rr.index[0]} was slowest (median {rr['median_min'].iloc[0]:.0f} min) "
               f"and {rr.index[-1]} fastest ({rr['median_min'].iloc[-1]:.0f} min)." if len(rr) > 1 else
               "Too few faults per region to rank regions reliably yet.")
eta_line = (f"{late_pct:.0%} of {len(eta)} faults with an estimate were restored later than estimated (median error {eta_med:.0f} min)."
            if len(eta) else "No estimate data available.")
readme = f"""# Network Fault Restoration Analysis (SP Energy Networks open data)

## Question
Where and how quickly are faults restored across SP Manweb and SP Distribution, and how accurate are the estimated restoration times?

*Why it matters:* [write one sentence in your own words: customers, performance reporting, resource planning]

## Data
- Source: SP Energy Networks Open Data Portal, National Energy Outage (NEO) Data: {DATASET_URL}
- Licence: [copy from the dataset's Information tab]
- {len(files)} daily export(s) combined; {len(clean)} completed, unplanned faults analysed, reported between {f0:%d %b %Y} and {f1:%d %b %Y}.

## Method
1. Combined daily exports and kept the latest record per Fault ID.
2. Cleaning (rows remaining after each step):

{log_df.to_markdown(index=False) if hasattr(log_df, 'to_markdown') else log_df.to_string(index=False)}

3. Calculated restoration time (minutes) and estimate error (actual minus estimated restoration).
4. Compared medians by voltage, licence area and region.
5. Mapped faults with Folium and grouped locations into hotspots with K-Means (Scikit-learn).

## Key findings (review and reword in your own words)
- Overall median restoration time was {overall:.0f} minutes across {len(clean)} faults. By voltage: {v_txt}.
- {region_line}
- {eta_line}
- {cluster_line}

## Outputs
![Voltage](outputs/chart1_voltage.png)
![Regions](outputs/chart2_regions.png)
![Distribution](outputs/chart3_distribution.png)
![Fault locations](outputs/fault_scatter_map.png)

Interactive maps: `outputs/fault_map.html` and `outputs/cluster_map.html` (open in a browser).

## Limitations
- Short time window from a live feed, so this is a snapshot, not a long-term trend.
- No cause-of-fault field, so I can't explain why some faults take longer.
- Clusters use location only and are indicative.

## How to run
```
pip install -r requirements.txt
python run_project.py "<dataset page URL>"
```
"""
(BASE / "README.md").write_text(readme, encoding="utf-8")
(BASE / ".gitignore").write_text("data/raw/\n__pycache__/\n.ipynb_checkpoints/\nproject_config.json\n")
print("\nDONE. Created charts, maps, cluster table and README.md in this folder. Open README.md and review it.")
