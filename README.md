# Network Fault Restoration Analysis (SP Energy Networks open data)

## Question
Where and how quickly are faults restored across SP Manweb and SP Distribution, and how accurate are the estimated restoration times?

*Why it matters:* [write one sentence in your own words: customers, performance reporting, resource planning]

## Data
- Source: SP Energy Networks Open Data Portal, National Energy Outage (NEO) Data: https://spenergynetworks.opendatasoft.com/explore/assets/national-energy-outage-data/
- Licence: [copy from the dataset's Information tab]
- 5 daily export(s) combined; 233 completed, unplanned faults analysed, reported between 29 Sep 2026 and 24 Oct 2026.

## Method
1. Combined daily exports and kept the latest record per Fault ID.
2. Cleaning (rows remaining after each step):

| Step                        |   Rows remaining |
|:----------------------------|-----------------:|
| Raw rows across all exports |              318 |
| Unique faults (by Fault ID) |              305 |
| Status = Completed/Restored |              269 |
| Unplanned only              |              251 |
| Has restoration time        |              251 |
| Restoration >= 1 min        |              233 |
| Coordinates inside UK       |              233 |

3. Calculated restoration time (minutes) and estimate error (actual minus estimated restoration).
4. Compared medians by voltage, licence area and region.
5. Mapped faults with Folium and grouped locations into hotspots with K-Means (Scikit-learn).

## Key findings (review and reword in your own words)
- Overall median restoration time was 153 minutes across 233 faults. By voltage: HV 176 min (n=80), LV 152 min (n=153).
- Among regions with at least 3 faults, OSWESTRY NORTH was slowest (median 311 min) and OSWESTRY SOUTH fastest (78 min).
- 3% of 206 faults with an estimate were restored later than estimated (median error -82 min).
- The largest hotspot contains 64 faults, mostly in DEE VALLEY SOUTH, with a median restoration time of 205 minutes.

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
