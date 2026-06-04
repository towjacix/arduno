from typing import cast

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import app.database as database


__all__ = ["router"]

router = APIRouter()


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
        }
    )
