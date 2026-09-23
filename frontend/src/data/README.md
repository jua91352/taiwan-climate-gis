# GIS Data

## taiwan-counties.geojson

Taiwan county/city (直轄市、縣市) administrative boundaries — 22 features.

| Item        | Value |
| ----------- | ----- |
| Source      | 內政部國土測繪中心 (NLSC)「直轄市、縣市界線(TWD97經緯度)」 |
| Dataset     | https://data.gov.tw/dataset/7442 |
| Version     | `COUNTY_MOI_1140318` (SHP) |
| License     | 政府資料開放授權條款－第1版 (Open Government Data License, version 1.0) |
| CRS         | TWD97 geographic lon/lat (GRS80). Differs from WGS84 by < 1 m, used directly as EPSG:4326 |
| Properties  | `COUNTYNAME` (e.g. 臺中市), `COUNTYENG`, `COUNTYCODE` |

Converted from the official shapefile with [mapshaper](https://github.com/mbloch/mapshaper) 0.7.66:

```bash
mapshaper COUNTY_MOI_1140318.shp encoding=utf8 \
  -simplify interval=100 keep-shapes \
  -filter-fields COUNTYNAME,COUNTYENG,COUNTYCODE \
  -o taiwan-counties.geojson format=geojson precision=0.00001
```

Simplification (100 m tolerance) removes only islets smaller than the tolerance;
all inhabited/major outlying islands are retained.
