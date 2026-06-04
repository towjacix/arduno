# 🧠 Algorithmic Core of Arduno

This document details the mathematical and logical algorithms driving the Arduno Lab Safety Monitor. It explains how raw sensor data (temperature in °C and smoke in ADC) is translated into actionable alerts, hardware signals, and stable UI visualizations.

---

## 1. Dynamic Moving Average (DMA) Thresholding

Fire detection systems face a common problem: ambient room temperatures change throughout the day and across seasons. A fixed threshold of 35°C might cause false alarms in summer and fail to detect slow-burning fires in winter.

Arduno uses a **Dynamic Moving Average (DMA)** to constantly baseline the room's normal temperature:

```
Current Baseline = AVG(last N safe temperature readings)
Dynamic Threshold = Current Baseline + temp_offset
```

- **`N` (window_size):** Configured in `config.toml` (default: 10 readings, which equals 20 seconds at a 2s polling rate).
- **`temp_offset`:** Configured in `config.toml` (default: 10°C). If the room is 25°C, the system alerts at 35°C.

**Crucial Logic:** Readings are *only* added to the moving average when the system is in a `safe` state. If an incident starts (`critical`), the baseline freezes. This prevents the threshold from drifting upward and masking a growing fire.

## 2. Hysteresis for State Stability

When a temperature hovers exactly around the threshold (e.g., oscillating between 35.0°C and 34.9°C), a naive system would rapidly toggle between "critical" and "safe" states. This causes UI flicker and chaotic buzzer alarms.

Arduno implements **Hysteresis** (default 2°C) to create a dead-zone between triggering an alert and clearing it:

- **Trigger Critical:** `temp > threshold` OR `smoke > smoke_limit`
- **Return to Safe:** `temp ≤ (threshold - hysteresis)` AND `smoke ≤ smoke_limit`

If the threshold is 35°C, it triggers at > 35°C, but will not return to safe until it drops to ≤ 33°C.

## 3. Level Classification & Hardware Signaling

The backend determines the severity of the situation across three levels: `safe`, `warning`, and `critical`. These levels are sent back to the Arduino to drive the physical buzzer with Morse code.

To unify temperature and smoke readings (which have vastly different units), Arduno normalizes them into a shared percentage scale using **Fixed Scale Ceilings**:

- **Temp Ceiling:** 70°C (derived from `default_temp 45 / 0.70`)
- **Smoke Ceiling:** 500 ADC (derived from `default_smoke 300 / 0.70`)

```python
temp_pct  = min(100.0, (temp / 70.0) * 100.0)
smoke_pct = min(100.0, (smoke / 500.0) * 100.0)
peak_pct  = max(temp_pct, smoke_pct)
```

The `peak_pct` dictates the level:
- **`peak_pct < 60%`:** `safe` (Buzzer silent)
- **`60% ≤ peak_pct < 70%`:** `warning` (Buzzer plays Morse 'W' `·──`)
- **`peak_pct ≥ 70%`:** `critical` (Buzzer plays Morse 'SOS' `···───···`)

*Note: 70% on this scale mathematically aligns exactly with the configured physical critical thresholds (45°C / 300 ADC).*

## 4. UI Chart Normalization

The live chart (Chart.js) also relies on the **Fixed Scale Ceilings** mentioned above. 

Historically, charts dynamically scale their Y-axis based on the minimum and maximum values currently visible in the window. However, in a rolling 30-point window, when a historical peak drops out of view, the entire graph's scale recalculates, causing all current bars/lines to visually jump upwards.

By normalizing all incoming points against the absolute ceilings (`scale_temp_max` and `scale_smoke_max` provided by the API) and capping the chart at 100%, the Y-axis remains locked. The 70% line always perfectly represents the critical threshold, guaranteeing a stable, non-jumping visual representation as time scrolls forward.
