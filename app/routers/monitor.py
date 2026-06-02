import datetime

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

import app.database as database
from app.config import CONFIG
from app.schemas import MonitorPayload


router = APIRouter()


async def get_dynamic_threshold() -> int:
    """Tính toán ngưỡng động với Type Safety và bộ lọc loại bỏ giá trị cháy."""
    if CONFIG["mode"] != "auto" or database.db is None:
        return int(CONFIG.get_nested("threshold", "default", "temp", default=45))

    window = int(CONFIG["window_size"])
    offset = int(CONFIG["temp_offset"])

    # Chỉ tính trung bình của các điểm dữ liệu nhỏ hơn ngưỡng hiện tại
    query = """
        SELECT AVG(temp) FROM (
            SELECT temp FROM burning_logs 
            WHERE temp < (SELECT current_dynamic_threshold FROM system_state WHERE id=1)
            ORDER BY id DESC LIMIT ?
        )
    """
    res = await database.db.execute(query, [window])
    avg_val = res.rows[0][0]

    base_temp = float(avg_val) if isinstance(avg_val, (int, float)) else 30.0
    return int(base_temp + offset)


@router.post("/api/monitor")
async def monitor_system(data: MonitorPayload):
    if database.db is None:
        return Response(status_code=500)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Lấy trạng thái hiện tại
    state_res = await database.db.execute(
        "SELECT status FROM system_state WHERE id = 1"
    )
    old_status = (
        str(state_res.rows[0][0])
        if (state_res.rows and state_res.rows[0][0] is not None)
        else "safe"
    )

    threshold = await get_dynamic_threshold()
    smoke_limit = int(CONFIG.get_nested("threshold", "default", "smoke", default=300))

    new_status = (
        "critical" if data.temp > threshold or data.smoke > smoke_limit else "safe"
    )

    active_inc_id = 0

    # LOGIC XỬ LÝ SỰ CỐ & CẬP NHẬT PEAK TEMP LIÊN TỤC
    if new_status == "critical":
        if old_status == "safe":
            await database.db.execute(
                "INSERT INTO incidents (start_time, end_time, peak_temp) VALUES (?, 'Active', ?)",
                [now, data.temp],
            )

        inc_res = await database.db.execute(
            "SELECT incident_id, peak_temp FROM incidents WHERE end_time = 'Active' LIMIT 1"
        )
        if inc_res.rows:
            raw_inc_id = inc_res.rows[0][0]
            raw_peak = inc_res.rows[0][1]

            active_inc_id = int(raw_inc_id) if isinstance(raw_inc_id, int) else 0
            old_peak = float(raw_peak) if isinstance(raw_peak, (int, float)) else 0.0

            if data.temp > old_peak:
                await database.db.execute(
                    "UPDATE incidents SET peak_temp = ? WHERE incident_id = ?",
                    [data.temp, active_inc_id],
                )

    elif old_status == "critical" and new_status == "safe":
        await database.db.execute(
            "UPDATE incidents SET end_time = ? WHERE end_time = 'Active'", [now]
        )

    # Ghi log liên tục bất kể trạng thái để duy trì cửa sổ trượt mượt mà
    await database.db.execute(
        "INSERT INTO burning_logs (incident_id, timestamp, temp, smoke) VALUES (?, ?, ?, ?)",
        [active_inc_id, now, data.temp, data.smoke],
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
        return HTMLResponse("<div>Offline</div>")

    res = await database.db.execute(
        "SELECT status, temp, smoke, current_dynamic_threshold, timestamp "
        "FROM system_state WHERE id=1"
    )
    if not res.rows:
        return HTMLResponse("<div>No data</div>")

    r = res.rows[0]

    # Gán vào các biến tạm để Basedpyright thực hiện Type Narrowing chính xác
    raw_status, raw_temp, raw_smoke, raw_thresh, raw_ts = r[0], r[1], r[2], r[3], r[4]

    status = str(raw_status) if raw_status is not None else "safe"
    temp = float(raw_temp) if isinstance(raw_temp, (int, float)) else 0.0
    smoke = int(raw_smoke) if isinstance(raw_smoke, int) else 0
    thresh = int(raw_thresh) if isinstance(raw_thresh, int) else 45
    ts = str(raw_ts) if raw_ts is not None else ""

    status_class = "critical-bg white-text" if status == "critical" else ""
    status_icon = "warning" if status == "critical" else "check_circle"

    # Định dạng các khối phân mảnh HTML
    temp_frag = (
        '<div class="temp-data"><div class="row"><i class="orange-text">thermostat</i>'
        f'<div class="max"><h6>Nhiệt độ</h6><h4>{temp}°C</h4></div></div></div>'
    )
    smoke_frag = (
        '<div class="smoke-data"><div class="row"><i class="grey-text">cloud</i>'
        f'<div class="max"><h6>Mật độ khói</h6><h4>{smoke} PPM</h4></div></div></div>'
    )
    thresh_frag = (
        '<div class="threshold-data"><div class="row"><i class="blue-text">psychology</i>'
        f'<div class="max"><h6>Ngưỡng tự động</h6><h4>{thresh}°C</h4></div></div></div>'
    )
    status_frag = (
        f'<div class="system-status-card {status_class}">'
        f'<article class="border padding center-align"><h5>'
        f'<i class="small">{status_icon}</i> Hệ thống: {status.upper()}</h5>'
        f"<p>{ts}</p></article></div>"
    )

    html_content = f"{temp_frag}{smoke_frag}{thresh_frag}{status_frag}"
    return HTMLResponse(content=html_content)


@router.get("/api/history", response_class=HTMLResponse)
async def get_history_html():
    if database.db is None:
        return HTMLResponse("")
    res = await database.db.execute(
        "SELECT incident_id, start_time, end_time, peak_temp "
        "FROM incidents ORDER BY 1 DESC LIMIT 10"
    )

    rows = []
    for r in res.rows:
        inc_id_raw, start_time_raw, end_time_raw, peak_temp_raw = r[0], r[1], r[2], r[3]

        inc_id = int(inc_id_raw) if isinstance(inc_id_raw, int) else 0
        start_time = str(start_time_raw) if start_time_raw is not None else ""
        dest_time = str(end_time_raw) if end_time_raw is not None else ""
        peak_temp = (
            float(peak_temp_raw) if isinstance(peak_temp_raw, (int, float)) else 0.0
        )

        btn_class = "error" if dest_time == "Active" else "outline"
        btn_label = "LIVE" if dest_time == "Active" else "DONE"

        row = (
            f"<tr><td>#{inc_id}</td><td>{start_time}</td><td>{dest_time}</td>"
            f"<td>{peak_temp}°C</td><td>"
            f"<button class='chip {btn_class}'>{btn_label}</button></td></tr>"
        )
        rows.append(row)

    return HTMLResponse(
        "".join(rows) if rows else "<tr><td colspan='5'>No history</td></tr>"
    )
