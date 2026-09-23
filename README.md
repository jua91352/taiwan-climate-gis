# Taiwan Climate GIS Dashboard

以台灣 GIS 地圖為核心的氣象資料視覺化平台。

> 目前狀態：Phase 0 — Project / Architecture Setup。
> 目前僅有專案結構，尚未實作任何功能。完整規格請見 [DESIGN.md](DESIGN.md)。

## Project Overview

資料流程：

```text
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

## Planned Features

以下功能皆為規劃中，尚未實作：

- 台灣 GIS 地圖（標準、暗色、衛星底圖）
- CWA 測站與 Station Popup
- 22 縣市邊界、Tooltip、點擊縣市 Zoom
- 縣市目前氣象資訊
- SQLite 歷史氣象資料累積
- 縣市歷史氣溫 Chart（24 小時 / 7 天 / 30 天）
- 氣象圖層控制
- Light / Dark Mode
- Vercel 部署

## Technology Stack

| Layer    | Technology                                            |
| -------- | ----------------------------------------------------- |
| Backend  | Python 3.x, Flask, Flask-CORS, requests, python-dotenv |
| Database | SQLite（Python 內建 `sqlite3`）                         |
| Frontend | Vite, TypeScript, Leaflet                             |
| GIS      | GeoJSON, OpenStreetMap, Esri World Imagery            |
| Deploy   | GitHub, Vercel                                        |

## Data Source

- 中央氣象署（CWA）開放資料：**O-A0003-001**（現在天氣觀測報告）
- CWA API Key 只存在 Backend，Frontend 不直接呼叫 CWA API。
- 請複製 `.env.example` 為 `.env` 並填入自己的 `CWA_API_KEY`。`.env` 不得提交至 Git。

## Project Structure

```text
HW1_923/
├── DESIGN.md
├── README.md
├── requirements.txt
├── .gitignore
├── .env.example
├── backend/
├── frontend/
├── data/
└── tests/
```

## Development Phases

| Phase | 內容                                   | 狀態     |
| ----- | -------------------------------------- | -------- |
| 0     | Project / Architecture Setup           | 進行中   |
| 1     | Flask Backend Skeleton                 | 尚未開始 |
| 2     | SQLite Database                        | 尚未開始 |
| 3     | CWA O-A0003-001 Ingestion              | 尚未開始 |
| 4     | Vite + TypeScript + Leaflet Frontend   | 尚未開始 |
| 5     | GIS Map                                | 尚未開始 |
| 6     | Flask API Integration                  | 尚未開始 |
| 7     | County Current Weather                 | 尚未開始 |
| 8     | Historical Data API                    | 尚未開始 |
| 9     | Historical Chart                       | 尚未開始 |
| 10    | Map Themes                             | 尚未開始 |
| 11    | Weather Layers                         | 尚未開始 |
| 12    | UI Polish                              | 尚未開始 |
| 13    | Testing                                | 尚未開始 |
| 14    | GitHub                                 | 尚未開始 |
| 15    | Vercel                                 | 尚未開始 |
