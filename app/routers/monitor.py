import datetime
from typing import cast

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

import app.database as database
from app.config import CONFIG
from app.schemas import MonitorPayload


router = APIRouter()


async def get_dynamic_threshold() -> int:
    if CONFIG["mode"] != "auto" or database.db is None:
        return int(CONFIG.get_nested("threshold", "default", "temp", default=45))

    query = (
        "SELECT AVG(temp) FROM (SELECT temp FROM burning_logs ORDER BY id DESC LIMIT ?)"
    )
    res = await database.db.execute(query, [int(CONFIG["window_size"])])
    avg_val = res.rows[0][0]
    base_temp = float(avg_val) if isinstance(avg_val, (int, float)) else 30.0
    return int(base_temp + int(CONFIG["temp_offset"]))


@router.post("/api/monitor")
async def monitor_system(data: MonitorPayload):
    if database.db is None:
        return Response(status_code=500)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state_res = await database.db.execute(
        "SELECT status FROM system_state WHERE id = 1"
    )
    old_status = str(state_res.rows[0][0]) if state_res.rows else "safe"

    threshold = await get_dynamic_threshold()
    new_status = "critical" if data.temp > threshold or data.smoke > 300 else "safe"

    if new_status == "critical":
        if old_status == "safe":
            await database.db.execute(
                "INSERT INTO incidents (start_time, end_time, peak_temp) VALUES (?, 'Active', ?)",
                [now, data.temp],
            )
        await database.db.execute(
            "INSERT INTO burning_logs (incident_id, timestamp, temp, smoke) "
            "VALUES ((SELECT incident_id FROM incidents ORDER BY 1 DESC LIMIT 1), ?, ?, ?)",
            [now, data.temp, data.smoke],
        )

    await database.db.execute(
        "UPDATE system_state SET status=?, timestamp=?, temp=?, smoke=?, "
        "current_dynamic_threshold=? WHERE id=1",
        [new_status, now, data.temp, data.smoke, threshold],
    )
    return {"status": new_status}


@router.get("/api/status", response_class=HTMLResponse)
async def get_status_html():
    if database.db is None:
        return HTMLResponse("Offline")
    res = await database.db.execute(
        "SELECT status, temp, smoke, current_dynamic_threshold, timestamp FROM system_state WHERE id=1"
    )
    r = res.rows[0]
    status, temp, smoke, thresh, ts = (
        str(r[0]),
        float(cast(float, r[1])),
        int(cast(int, r[2])),
        int(cast(int, r[3])),
        str(r[4]),
    )

    status_class = "critical-bg white-text" if status == "critical" else ""
    return f"""
    <div class="temp-data"><div class="row"><i class="orange-text">thermostat</i><div class="max"><h6>Nhiệt độ</h6><h4>{temp}°C</h4></div></div></div>
    <div class="smoke-data"><div class="row"><i class="grey-text">cloud</i><div class="max"><h6>Mật độ khói</h6><h4>{smoke} PPM</h4></div></div></div>
    <div class="threshold-data"><div class="row"><i class="blue-text">psychology</i><div class="max"><h6>Ngưỡng tự động</h6><h4>{thresh}°C</h4></div></div></div>
    <div class="system-status-card {status_class}"><article class="border padding center-align"><h5>Hệ thống: {status.upper()}</h5><p>{ts}</p></article></div>
    """


@router.get("/api/history", response_class=HTMLResponse)
async def get_history_html():
    if database.db is None:
        return HTMLResponse("")
    res = await database.db.execute(
        "SELECT incident_id, start_time, end_time, peak_temp FROM incidents ORDER BY 1 DESC LIMIT 10"
    )
    rows = [
        f"<tr><td>#{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}°C</td><td>"
        f"<button class='chip {'error' if r[2] == 'Active' else 'outline'}'>{'LIVE' if r[2] == 'Active' else 'DONE'}</button></td></tr>"
        for r in res.rows
    ]
    return HTMLResponse(
        "".join(rows) if rows else "<tr><td colspan='5'>No history</td></tr>"
    )
