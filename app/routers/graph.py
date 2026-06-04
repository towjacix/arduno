from typing import cast

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import app.database as database
from app.config import CONFIG


__all__ = ["router"]

router = APIRouter()

# Fixed Y-axis scale ceilings.
# These are sized so the configured critical thresholds land at exactly 70%:
#   temp  threshold (default 45°C) / 0.70 ≈ 64 → rounded up to 70°C
#   smoke threshold (default 300 ADC) / 0.70 ≈ 428 → rounded up to 500 ADC
# Keeping these fixed means the graph NEVER rebuilds its scale when peak
# values roll out of the window.
_TEMP_SCALE_MAX  = float(CONFIG.get_nested("ui", "graph", "temp_scale_max",  default=70))
_SMOKE_SCALE_MAX = float(CONFIG.get_nested("ui", "graph", "smoke_scale_max", default=500))

# Band thresholds (percentage of full scale)
_WARN_PCT     = 60.0   # safe → warning boundary
_CRITICAL_PCT = 70.0   # warning → critical boundary


@router.get("/api/analytics/data/{incident_id}")
async def get_chart_data(incident_id: str):
    """Return raw sensor data as JSON for the browser-side Chart.js renderer.

    Live mode ("latest"):  always returns the last 30 readings from burning_logs,
                           regardless of incident status — the chart grows naturally
                           over time and never resets to a single centred bar.
    History mode ("<id>"): returns every reading for that specific incident.
    """
    if database.db is None:
        return JSONResponse({"error": "Database Error"}, status_code=500)

    is_latest = incident_id == "latest"
    target_id: int = 0
    current_status = "safe"
    is_active = False

    # ── Validate history id ────────────────────────────────────────────────
    if not is_latest:
        try:
            target_id = int(incident_id)
        except ValueError:
            return JSONResponse({"error": "Invalid incident ID"}, status_code=400)

    # ── System status (latest only) ────────────────────────────────────────
    if is_latest:
        state_res = await database.db.execute(
            "SELECT status FROM system_state WHERE id = 1"
        )
        if state_res.rows:
            current_status = str(state_res.rows[0][0])

    # ── Query rows ────────────────────────────────────────────────────────
    # Live mode: ALWAYS show last 30 readings regardless of incident status.
    # This is the key fix — the chart never resets to 1 point when a new
    # incident starts; it keeps the rolling window and grows naturally.
    if is_latest:
        res = await database.db.execute(
            "SELECT temp, smoke, timestamp FROM ("
            "  SELECT id, temp, smoke, timestamp FROM burning_logs"
            "  ORDER BY id DESC LIMIT 30"
            ") AS recent ORDER BY id ASC"
        )
    else:
        res = await database.db.execute(
            "SELECT temp, smoke, timestamp FROM burning_logs"
            " WHERE incident_id = ? ORDER BY id ASC",
            [target_id],
        )
        id_res = await database.db.execute(
            "SELECT end_time FROM incidents WHERE incident_id = ?", [target_id]
        )
        if id_res.rows:
            is_active = str(id_res.rows[0][0]) == "Active"

    # ── Parse rows ────────────────────────────────────────────────────────
    points_t: list[float] = []
    points_s: list[float] = []
    times: list[str] = []

    for row in res.rows:
        t_val, s_val, ts = row[0], row[1], row[2]
        if isinstance(t_val, (int, float)) and isinstance(s_val, (int, float)):
            points_t.append(float(t_val))
            points_s.append(float(s_val))
            ts_str = str(ts) if ts is not None else ""
            times.append(ts_str.split(" ")[1] if " " in ts_str else ts_str)

    return JSONResponse(
        {
            "times": times,
            "temp": points_t,
            "smoke": points_s,
            "status": current_status,
            "incident_id": incident_id,
            "is_active": is_active,
            # Fixed scale anchors — client MUST use these for normalization.
            # Never derive scale from window min/max.
            "scale_temp_max":  _TEMP_SCALE_MAX,
            "scale_smoke_max": _SMOKE_SCALE_MAX,
            "warn_pct":        _WARN_PCT,
            "critical_pct":    _CRITICAL_PCT,
        }
    )
