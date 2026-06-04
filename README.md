# 🔥 Arduno — Lab Safety Monitor

A cloud-native IoT fire and smoke monitoring system. An Arduino Uno reads temperature (DHT22) and smoke (MQ-2) sensors and reports to a FastAPI backend deployed on Vercel, which stores data in Turso (LibSQL) and serves a live HTMX dashboard.

---

## 🏗️ Architecture

```
Arduino Uno + ESP-01S
        │  (HTTP POST every 2s via AT commands)
        ▼
   Vercel Serverless
  ┌─────────────────────────────────┐
  │  FastAPI  (api/index.py)        │
  │  ├── POST /api/monitor          │  ← sensor data ingestion
  │  ├── GET  /api/status           │  ← live status cards (HTMX)
  │  ├── GET  /api/history          │  ← incident log & search (HTMX)
  │  └── GET  /api/analytics/data   │  ← JSON telemetry for Chart.js
  └──────────────┬──────────────────┘
                 │
           Turso Edge DB (LibSQL)
```

The frontend is a single HTML file served at `/`. It uses **Beer CSS** for styling, **HTMX** for live DOM updates, and **Chart.js** for real-time telemetry rendering.

---

## 🧠 Algorithms & Logic

Arduno doesn't just read sensors; it analyzes them intelligently using:
- **Dynamic Moving Average (DMA)** to auto-baseline ambient temperatures.
- **Hysteresis** to prevent state flickering.
- **Fixed-Scale Normalization** for rock-solid UI charting without visual jumps.
- **Three-Tier Classification** (Safe, Warning, Critical) to drive Arduino buzzer Morse code alerts.

👉 **Read the full breakdown in [docs/algorithm.md](docs/algorithm.md)**

---

## 🔌 Hardware

| Component | Role | Pin |
|-----------|------|-----|
| Arduino Uno | Main controller | — |
| ESP-01S (ESP8266) | WiFi via AT commands | D2 (RX), D3 (TX) |
| DHT22 | Temperature & humidity | D4 |
| MQ-2 | Smoke / gas (raw ADC) | A0 |
| Buzzer (Active/Passive)| Morse code alerts | D5 |

### Wiring diagram

```
Arduino Uno          ESP-01S
───────────          ────────
D2 (RX) ◄─────────  TX
D3 (TX) ──[1kΩ]──┬─ RX      ← voltage divider (5V → 3.3V)
                 └─[2kΩ]─ GND
3.3V    ──────────  VCC
3.3V    ──────────  CH_PD (EN)
GND     ──────────  GND

DHT22: DATA → D4 | VCC → 5V | GND → GND
MQ-2:  AOUT → A0 | VCC → 5V | GND → GND
BUZZ:  (+)  → D5 | (-) → GND
```

> ⚠️ The ESP-01S runs on **3.3V**. Never connect VCC directly to Arduino's 5V pin.
> The voltage divider on D3 → ESP RX is required to avoid damaging the module.

---

## 🗄️ Database Schema

```sql
CREATE TABLE system_state (
    id                        INTEGER PRIMARY KEY,
    status                    TEXT,
    temp                      REAL,
    smoke                     INTEGER,
    current_dynamic_threshold INTEGER,
    timestamp                 TEXT
);

CREATE TABLE incidents (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time  TEXT,
    end_time    TEXT,   -- 'Active' while ongoing
    peak_temp   REAL
);

CREATE TABLE burning_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER,   -- 0 = ambient (no incident)
    timestamp   TEXT,
    temp        REAL,
    smoke       INTEGER
);

-- Seed the single system_state row
INSERT INTO system_state VALUES (1, 'safe', 0, 0, 45, '');
```

---

## ⚙️ Configuration

Edit `config/config.toml`:

```toml
# "auto" = Dynamic Moving Average threshold (recommended)
# "manual" = fixed thresholds below
mode = "auto"

# DMA: threshold = average(last window_size safe readings) + temp_offset
temp_offset = 10
window_size = 10   # number of samples (1 sample = 2s → 20s window)

# Hysteresis: prevents state flicker when temp sits at the boundary.
hysteresis = 2

[threshold.default]
temp  = 45    # °C — used in manual mode or as cold-start fallback
smoke = 300   # MQ-2 raw ADC value (normal air: 80–150, fire: >300)

[ui.graph]
temp_scale_max  = 70   # Fixed chart ceiling for temp (45 / 0.7)
smoke_scale_max = 500  # Fixed chart ceiling for smoke (300 / 0.7)
```

---

## 🚀 Local Development

### Requirements

- Python 3.11+
- Arduino IDE with libraries: **DHT sensor library** (Adafruit), **Adafruit Unified Sensor**

### Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export TURSO_DATABASE_URL="libsql://your-db.turso.io"
export TURSO_AUTH_TOKEN="your-token"

# Run
uvicorn api.index:app --reload
```

The dashboard is then available at `http://localhost:8000`.

### Firmware

1. Open `arduno.ino` in Arduino IDE.
2. Set your credentials at the top of the file:
   ```cpp
   const char* WIFI_SSID     = "your_ssid";
   const char* WIFI_PASSWORD = "your_password";
   const char* API_HOST      = "your-project.vercel.app";
   ```
3. Flash the ESP-01S AT firmware and set baud rate to 9600:
   ```
   AT+UART_DEF=9600,8,1,0,0
   ```
4. Upload the sketch to the Arduino Uno (board: **Arduino Uno**, port: your COM/tty).

---

## ☁️ Deployment (Vercel)

```bash
npm i -g vercel
vercel --prod
```

Set these environment variables in the Vercel dashboard:

| Variable | Value |
|----------|-------|
| `TURSO_DATABASE_URL` | `libsql://your-db.turso.io` |
| `TURSO_AUTH_TOKEN` | your Turso auth token |

The `vercel.json` already routes all `/api/*` requests and `/` to the FastAPI app.

---

## 📁 Project Structure

```text
arduno/
├── api/
│   └── index.py              # FastAPI app entry point (Vercel handler)
├── app/
│   ├── config.py             # TOML config loader
│   ├── database.py           # Turso LibSQL client
│   ├── schemas.py            # Pydantic request/response models
│   └── routers/
│       ├── monitor.py        # /api/monitor, /api/status, /api/history
│       └── graph.py          # /api/analytics/data/{incident_id}
├── beer_css_framework/
│   └── webpage.html          # Single-page dashboard (Beer CSS + HTMX + Chart.js)
├── config/
│   └── config.toml           # Runtime configuration
├── docs/
│   └── algorithm.md          # Core algorithms documentation
├── arduno.ino                # Arduino Uno + ESP-01S firmware
├── requirements.txt
└── vercel.json
```
