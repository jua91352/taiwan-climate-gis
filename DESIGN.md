# Taiwan Climate GIS Dashboard --- DESIGN.md

## 1. Project Overview

本專案是一個以台灣 GIS 地圖為核心的氣象資料視覺化平台。

核心流程：

``` text
CWA Open Data
    ↓
O-A0003-001
    ↓
Flask Backend
    ↓
JSON 解析 / 資料清洗 / 驗證
    ↓
SQLite
    ↓
歷史氣象資料累積
    ↓
Flask API
    ↓
Vite + TypeScript + Leaflet
    ↓
GIS 地圖 + 氣象圖層 + 歷史 Chart
    ↓
GitHub
    ↓
Vercel
```

老師課程的核心流程保留為：

``` text
API → JSON → Python → SQLite → Web App → GitHub
```

但最終 Web App 不使用 Streamlit，而使用 Flask +
Vite/TypeScript/Leaflet。

------------------------------------------------------------------------

## 2. Project Goals

### 課程要求

1.  CWA API 資料取得
2.  JSON 資料解析
3.  Python 資料處理
4.  SQLite 資料儲存
5.  Web App 呈現
6.  GitHub 版本管理
7.  GIS 地圖視覺化

### 專案延伸

1.  台灣 GIS 地圖
2.  標準、暗色、衛星底圖切換
3.  CWA 測站資料
4.  縣市邊界
5.  點擊縣市自動 Zoom
6.  即時氣象資訊
7.  SQLite 歷史氣象資料
8.  縣市歷史氣象 Chart
9.  氣象圖層控制
10. GitHub
11. Vercel 部署

------------------------------------------------------------------------

## 3. Technology Stack

### Backend

``` text
Python 3.x
Flask
Flask-CORS
requests
python-dotenv
sqlite3
```

### Frontend

``` text
Vite
TypeScript
Leaflet
```

### Database

``` text
SQLite
```

### GIS

``` text
Leaflet
GeoJSON
OpenStreetMap
Esri World Imagery
```

### Deployment

``` text
GitHub
Vercel
```

------------------------------------------------------------------------

## 4. Data Source

主要資料來源：

``` text
CWA O-A0003-001
```

主要欄位：

``` text
station_id
station_name
county_name
town_name
latitude
longitude
observation_time
temperature
humidity
wind_speed
wind_direction
uv_index
```

Frontend 不直接呼叫 CWA API。

CWA API Key 必須留在 Flask Backend。

資料流程：

``` text
CWA O-A0003-001
        ↓
Flask
        ↓
Fetch JSON
        ↓
Parse
        ↓
Normalize
        ↓
Validate
        ↓
SQLite
```

------------------------------------------------------------------------

## 5. System Architecture

``` text
                         ┌────────────────────┐
                         │    CWA Open Data   │
                         │    O-A0003-001     │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   Flask Backend    │
                         │                    │
                         │ CWA Client         │
                         │ Parser             │
                         │ Validator          │
                         │ Database Service   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │      SQLite        │
                         │                    │
                         │ Station            │
                         │ WeatherObservation│
                         └─────────┬──────────┘
                                   │
                      ┌────────────┴────────────┐
                      ▼                         ▼
             Latest Weather API        Historical Weather API
                      │                         │
                      └────────────┬────────────┘
                                   ▼
                         ┌────────────────────┐
                         │      Frontend      │
                         │ Vite + TypeScript  │
                         │ Leaflet            │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │     GIS Map        │
                         │ Base Maps          │
                         │ Weather Layers     │
                         │ Stations           │
                         │ County Boundaries  │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         Historical Charts
```

------------------------------------------------------------------------

## 6. Initial Project State

本專案是全新的 Greenfield 專案，不沿用任何舊專案程式碼。

專案初始狀態：

``` text
HW1_923/
└── DESIGN.md
```

目前狀態：

-   目前尚無 `frontend/`
-   目前尚無 `backend/`
-   目前尚無 SQLite database
-   目前尚無 Flask
-   目前尚無 Vite
-   目前尚無 Leaflet
-   目前尚無 CWA API implementation
-   目前尚無 GIS map
-   目前尚無 historical chart
-   目前尚無 Git repository

所有功能皆依照第 21 節 Development Phases，從 Phase 0 開始逐步實作。

最終目標不變：

``` text
CWA O-A0003-001
→ Flask
→ JSON parsing / validation
→ SQLite
→ Historical data
→ Flask API
→ Vite + TypeScript + Leaflet
→ GIS map
→ County interaction
→ Current weather
→ Historical Chart
→ GitHub
→ Vercel
```

------------------------------------------------------------------------

## 7. GIS Map Design

### Initial View

``` text
Center: [23.7, 120.9]
Zoom: 7
```

### Base Maps

至少提供：

``` text
□ 標準地圖
□ 暗色地圖
□ 衛星地圖
```

### Weather Layers

第一階段：

``` text
□ CWA 測站
□ 氣溫
□ 縣市邊界
```

後續：

``` text
□ 濕度
□ 風速
□ 風向
□ UV
□ 降雨
□ 颱風
```

每個圖層可以獨立開關。

------------------------------------------------------------------------

## 8. Station Popup

每個有效測站至少顯示：

``` text
測站名稱
測站 ID
縣市 / 鄉鎮
觀測時間
氣溫
濕度
風速
風向
UV
```

缺值：

``` text
資料不足
```

不可直接把 `-99` 顯示給使用者。

------------------------------------------------------------------------

## 9. SQLite Design

### Station

``` sql
CREATE TABLE Station (
    station_id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    county_name TEXT,
    town_name TEXT,
    latitude REAL,
    longitude REAL
);
```

### WeatherObservation

``` sql
CREATE TABLE WeatherObservation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    temperature REAL,
    humidity REAL,
    wind_speed REAL,
    wind_direction REAL,
    uv_index REAL,
    precipitation REAL,
    FOREIGN KEY (station_id) REFERENCES Station(station_id)
);
```

Station 是測站主檔。

WeatherObservation 用來累積歷史觀測資料。

------------------------------------------------------------------------

## 10. Historical Data

核心延伸：

``` text
CWA
 ↓
取得觀測
 ↓
Normalize
 ↓
SQLite
 ↓
累積歷史
 ↓
Historical Query
 ↓
Chart
```

歷史資料必須由 SQLite 查詢。

不得使用目前 CWA 資料假裝歷史資料。

------------------------------------------------------------------------

## 11. County Interaction

使用者點擊縣市：

``` text
Map Click
    ↓
取得 county_name
    ↓
CITY_CENTERS
    ↓
map.setView()
    ↓
取得目前縣市氣象
    ↓
查 SQLite 歷史資料
    ↓
顯示 Chart
```

因此「點擊縣市」是整個系統的重要互動入口。

### County Zoom 規則

縣市點擊 Zoom 必須使用 `CITY_CENTERS`（各縣市中心點與縮放層級）+
`map.setView()`。

**不可使用整個 MultiPolygon 的 `fitBounds()`**，避免高雄、宜蘭等縣市的
遠方離島造成縮放異常。

`CITY_CENTERS` 在 Phase 5 建立並經實際測試後，不可自行更改。

至少需實際測試：

-   臺中市
-   臺北市
-   高雄市
-   宜蘭縣

------------------------------------------------------------------------

## 12. Flask API

### 最新全台資料

``` http
GET /api/weather/latest
```

### 指定縣市目前資料

``` http
GET /api/weather/county/<county_name>
```

### 歷史資料

``` http
GET /api/weather/history?county=<county_name>&days=7
```

### 測站資料

``` http
GET /api/stations
```

### 單一測站

``` http
GET /api/weather/station/<station_id>
```

### Health Check

``` http
GET /api/health
```

------------------------------------------------------------------------

## 13. Historical Chart

點擊縣市後，至少提供：

``` text
24 小時
7 天
30 天
```

第一階段：

``` text
氣溫歷史趨勢
```

X 軸：

``` text
時間
```

Y 軸：

``` text
°C
```

後續可以增加：

``` text
濕度
風速
雨量
```

但不要第一階段全部同時實作。

------------------------------------------------------------------------

## 14. County Information Panel

``` text
┌──────────────────────────┐
│ 臺中市                   │
├──────────────────────────┤
│ 目前氣溫：27.8°C         │
│ 濕度：78%                │
│ 平均風速：2.4 m/s        │
│ 最新觀測：21:00          │
├──────────────────────────┤
│ 歷史氣溫                 │
│                          │
│        Chart             │
│                          │
├──────────────────────────┤
│ [24小時] [7天] [30天]   │
└──────────────────────────┘
```

------------------------------------------------------------------------

## 15. UI Theme

至少提供：

``` text
Light Mode
Dark Mode
```

Dark Mode 必須同步考慮：

-   Map
-   Panel
-   Popup
-   Chart
-   LayerControl
-   Buttons
-   Legend
-   Tooltip
-   Header

------------------------------------------------------------------------

## 16. UI Structure

``` text
┌─────────────────────────────────────────────┐
│ Header                                      │
│ Taiwan Climate GIS Dashboard                │
│ CWA O-A0003-001 | Last Update               │
├─────────────────────────────────────────────┤
│                                             │
│                 GIS MAP                     │
│                                             │
├─────────────────────────────────────────────┤
│ Map / Layer Controls                        │
├──────────────────────┬──────────────────────┤
│ County Information   │ Historical Chart     │
│ Current Weather      │                      │
└──────────────────────┴──────────────────────┘
```

Desktop-first，Mobile Responsive 後續處理。

------------------------------------------------------------------------

## 17. Data Validation

Backend 必須處理：

``` text
null
""
"-99"
"-999"
"NA"
"X"
```

基本驗證：

``` text
latitude 有效
longitude 有效
station_id 存在
observation_time 有效
temperature 可轉數字
```

無效資料不得直接送到 Frontend。

------------------------------------------------------------------------

## 18. Security

CWA API Key：

``` text
只存在 Backend
```

使用：

``` env
CWA_API_KEY=your_key_here
```

GitHub：

``` text
.env
```

不得提交。

提供：

``` text
.env.example
```

Frontend 不可以取得 CWA API Key。

------------------------------------------------------------------------

## 19. CWA Fetch Strategy

目標：

``` text
CWA → Backend → SQLite → Frontend
```

不要讓每個瀏覽器直接呼叫 CWA。

如果 CWA 暫時失敗：

``` text
不要清空 SQLite
```

保留最後一次成功資料。

Frontend 應能顯示：

``` text
目前 CWA 暫時無法更新
目前顯示最後一次成功取得的資料
```

------------------------------------------------------------------------

## 20. Vercel Considerations

Frontend：

``` text
Vite → Vercel
```

Backend：

``` text
Flask → Vercel-compatible deployment
```

SQLite 的持久化必須特別確認。

不能假設 Serverless Function 的本地檔案可以永久保存歷史資料。

因此開發階段先完成：

``` text
Flask + SQLite
```

部署階段再確認正式的持久化資料方案。

不得為了部署而破壞 SQLite 資料模型。

------------------------------------------------------------------------

# 21. Development Phases

本專案採「一個 Phase、一個 Prompt」方式。

Agent 每次只能完成一個明確階段。

本專案從全新狀態開始（見第 6 節 Initial Project State）。

## Phase 0 --- Project / Architecture Setup

建立專案基礎結構，不實作功能：

-   專案目錄結構（`backend/`、`frontend/`、`data/`、`tests/`）
-   `.gitignore`（包含 `.env`、SQLite 檔、`node_modules/`、`venv/`）
-   `.env.example`
-   README 骨架
-   初始化 Git

## Phase 1 --- Flask Backend Skeleton

建立 Flask 基礎架構，先讓 Flask 可以啟動。

完成：

``` http
GET /api/health
```

## Phase 2 --- SQLite Database

建立 Station 與 WeatherObservation（見第 9 節）。

## Phase 3 --- CWA O-A0003-001 Ingestion

完成：

``` text
O-A0003-001
→ requests
→ JSON
→ parser
→ validation
→ SQLite
```

## Phase 4 --- Vite + TypeScript + Leaflet Frontend

建立 `frontend/`：

-   Vite + TypeScript 專案
-   安裝 Leaflet
-   顯示基本 Leaflet 地圖
-   TypeScript typecheck 通過
-   Vite build 通過

## Phase 5 --- GIS Map

完成：

-   全台初始視野（見第 7 節）
-   OSM 標準地圖
-   Esri Satellite
-   LayerControl
-   22 縣市 GeoJSON 邊界
-   縣市 Tooltip
-   Hover 加粗
-   `CITY_CENTERS`
-   點擊縣市 → `map.setView()` Zoom（見第 11 節 County Zoom 規則）

此階段 Frontend 不直接呼叫 CWA API，也不放入 CWA API Key。

## Phase 6 --- Flask API Integration

完成：

``` http
GET /api/weather/latest
GET /api/stations
GET /api/weather/station/<station_id>
```

Frontend 改由 Flask API 取得資料：

-   CWA 測站 Marker
-   Station Popup（見第 8 節）
-   缺值處理

保留 Phase 5 已完成的 GIS 功能。

## Phase 7 --- County Current Weather

完成：

``` http
GET /api/weather/county/<county_name>
```

點擊縣市後顯示該縣市目前氣象（見第 14 節）。

## Phase 8 --- Historical Data API

完成 SQLite 歷史查詢：

``` http
GET /api/weather/history?county=<county_name>&days=7
```

## Phase 9 --- Historical Chart

完成：

``` text
24H
7D
30D
```

第一階段只做 Temperature。

## Phase 10 --- Map Themes

加入：

``` text
Light
Dark
Satellite
```

## Phase 11 --- Weather Layers

逐步加入：

``` text
Station
Temperature
Humidity
Wind
Rain
UV
```

## Phase 12 --- UI Polish

整理 Header、Controls、Legend、Current Weather、Chart、Dark Mode。

## Phase 13 --- Testing

實際瀏覽器測試：

1.  Map
2.  Marker
3.  Popup
4.  County click
5.  Zoom
6.  API
7.  Historical Chart
8.  Dark
9.  Satellite
10. LayerControl

## Phase 14 --- GitHub

整理：

``` text
README
.env.example
.gitignore
Git history
```

確認沒有 API Key。

## Phase 15 --- Vercel

最後處理：

``` text
Vite
Flask
Environment Variables
API routing
Deployment
```

------------------------------------------------------------------------

# 22. Git Checkpoints

每個可驗證階段建立 checkpoint。

例如：

``` text
chore: initialize project structure
feat: add flask backend skeleton
feat: add sqlite database
feat: add cwa ingestion
feat: add vite typescript leaflet frontend
feat: add gis map with county boundaries
feat: connect frontend to flask api
feat: add county current weather
feat: add historical weather api
feat: add county historical chart
feat: add dark map layer
feat: add weather layer controls
feat: deploy to vercel
```

不要把所有功能塞進一個 commit。

------------------------------------------------------------------------

# 23. Agent Rules

Agent 必須遵守：

1.  最終 Web Framework 使用 Flask，不使用 Streamlit。
2.  Frontend 使用 Vite + TypeScript + Leaflet。
3.  CWA 使用 O-A0003-001。
4.  Frontend 不直接暴露 CWA API Key。
5.  歷史資料必須來自 SQLite。
6.  不可以用即時 CWA 資料假裝歷史資料。
7.  不可刪除已完成並測試過的 GIS 功能。
8.  `CITY_CENTERS` 建立並測試後，不可自行改變。
9.  縣市 click zoom 必須使用 `CITY_CENTERS` + `map.setView()`，不可使用
    `fitBounds()` 取代。
10. 每次只完成一個 Phase。
11. 未要求時不得擴大工作範圍。
12. 修改後必須測試。
13. `frontend/` 建立後（Phase 4 起），typecheck / build 必須維持通過。
14. 不得將 `.env`、API Key、Secret commit 到 GitHub。
15. 完成一個 Phase 後停止，等待下一個 Prompt。

------------------------------------------------------------------------

# 24. Definition of Done

### Data

-   [ ] CWA O-A0003-001
-   [ ] Flask 取得資料
-   [ ] JSON 正確解析
-   [ ] 無效資料處理
-   [ ] SQLite 儲存
-   [ ] 歷史資料累積

### Backend

-   [ ] Flask 啟動
-   [ ] `/api/health`
-   [ ] `/api/weather/latest`
-   [ ] `/api/weather/county/<county>`
-   [ ] `/api/weather/history`
-   [ ] `/api/stations`

### GIS

-   [ ] 台灣地圖
-   [ ] 標準底圖
-   [ ] 暗色底圖
-   [ ] 衛星底圖
-   [ ] 測站
-   [ ] 縣市界線
-   [ ] 點擊縣市 Zoom
-   [ ] Tooltip
-   [ ] Popup

### Historical Data

-   [ ] SQLite 歷史資料
-   [ ] 縣市歷史查詢
-   [ ] 24 小時 Chart
-   [ ] 7 天 Chart
-   [ ] 30 天 Chart

### Deployment

-   [ ] GitHub
-   [ ] Vercel
-   [ ] Environment Variables
-   [ ] API Key 未公開
-   [ ] Production 正常運作

------------------------------------------------------------------------

# 25. Final User Flow

``` text
開啟網站
   ↓
台灣 GIS Map
   ↓
選擇底圖
   ├── Light
   ├── Dark
   └── Satellite
   ↓
看到 CWA 測站
   ↓
點擊測站
   ↓
查看目前氣象
   ↓
點擊縣市
   ↓
地圖 Zoom
   ↓
顯示該縣市目前氣象
   ↓
查詢 SQLite
   ↓
顯示歷史氣象 Chart
   ↓
切換 24H / 7D / 30D
```

最終形成：

``` text
CWA API
+
Python / Flask
+
SQLite Historical Data
+
GIS
+
Time Series Chart
+
GitHub
+
Vercel
```

完整的「氣象資料取得 → 資料庫 → GIS → 歷史分析 → Web Deployment」作品。
