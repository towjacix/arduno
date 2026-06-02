import datetime
from typing import cast

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import CONFIG
from app.database import db
from app.schemas import MonitorPayload


router = APIRouter()


async def get_dynamic_threshold() -> int:
    """Tính toán ngưỡng động dựa trên Moving Average."""
    if CONFIG["mode"] != "auto":
        return int(CONFIG.get_nested("threshold", "default", "temp", default=45))

    window = int(CONFIG["window_size"])
    offset = int(CONFIG["temp_offset"])

    query = (
        "SELECT AVG(temp) FROM (SELECT temp FROM burning_logs ORDER BY id DESC LIMIT ?)"
    )
    res = await db.execute(query, [window])
    avg_val = res.rows[0][0]

    base_temp = float(avg_val) if isinstance(avg_val, (int, float)) else 30.0
    return int(base_temp + offset)


@router.post("/api/monitor")
async def monitor_system(data: MonitorPayload):
    """Tiếp nhận dữ liệu từ ESP-01 và xử lý sự cố."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Lấy trạng thái hiện tại
    state_res = await db.execute("SELECT status FROM system_state WHERE id = 1")
    old_status = str(state_res.rows[0][0]) if state_res.rows else "safe"

    threshold = await get_dynamic_threshold()
    smoke_limit = int(CONFIG.get_nested("threshold", "default", "smoke", default=300))

    new_status = "safe"
    if data.temp > threshold or data.smoke > smoke_limit:
        new_status = "critical"

    # 2. Xử lý Incident (Chuyển trạng thái)
    if new_status == "critical" and old_status == "safe":
        sql_inc = (
            "INSERT INTO incidents (start_time, end_time, peak_temp) "
            "VALUES (?, 'Active', ?)"
        )
        await db.execute(sql_inc, [now, data.temp])

    # 3. Ghi log nếu đang trong vụ sự cố
    if new_status == "critical":
        sql_log = (
            "INSERT INTO burning_logs (incident_id, timestamp, temp, smoke) "
            "VALUES ((SELECT incident_id FROM incidents ORDER BY 1 DESC LIMIT 1), "
            "?, ?, ?)"
        )
        await db.execute(sql_log, [now, data.temp, data.smoke])

    # 4. Cập nhật System State
    update_q = (
        "UPDATE system_state SET status = ?, timestamp = ?, temp = ?, "
        "smoke = ?, current_dynamic_threshold = ? WHERE id = 1"
    )
    await db.execute(update_q, [new_status, now, data.temp, data.smoke, threshold])

    return {"status": new_status, "threshold": threshold}


@router.get("/api/status", response_class=HTMLResponse)
async def get_status_html():
    """Trả về các mẩu HTML (Fragments) cho Dashboard Dashboard."""
    query = (
        "SELECT status, temp, smoke, current_dynamic_threshold, timestamp "
        "FROM system_state WHERE id = 1"
    )
    res = await db.execute(query)
    if not res.rows:
        return HTMLResponse(content="<div>No data</div>")

    row = res.rows[0]
    # Ép kiểu an toàn
    status = str(row[0])
    temp = float(cast(float, row[1]))
    smoke = int(cast(int, row[2]))
    threshold = int(cast(int, row[3]))

    # Class CSS động dựa trên trạng thái
    status_class = "critical-bg white-text" if status == "critical" else ""
    status_icon = "warning" if status == "critical" else "check_circle"

    # Trả về các khối HTML để HTMX hx-select bốc tách
    return f"""
    <div class="temp-data">
        <div class="row">
            <i class="orange-text">thermostat</i>
            <div class="max"><h6>Nhiệt độ</h6><h4>{temp}°C</h4></div>
        </div>
    </div>
    <div class="smoke-data">
        <div class="row">
            <i class="grey-text">cloud</i>
            <div class="max"><h6>Mật độ khói</h6><h4>{smoke} PPM</h4></div>
        </div>
    </div>
    <div class="threshold-data">
        <div class="row">
            <i class="blue-text">psychology</i>
            <div class="max"><h6>Ngưỡng tự động</h6><h4>{threshold}°C</h4></div>
        </div>
    </div>
    <div class="system-status-card {status_class}">
        <article class="border padding center-align">
            <i class="extra">{status_icon}</i>
            <h5>Hệ thống: {status.upper()}</h5>
            <p>Cập nhật: {row[4]}</p>
        </article>
    </div>
    """


@router.get("/api/history", response_class=HTMLResponse)
async def get_history_html():
    """Trả về danh sách <tr> cho bảng lịch sử."""
    query = (
        "SELECT incident_id, start_time, end_time, peak_temp "
        "FROM incidents ORDER BY incident_id DESC LIMIT 10"
    )
    res = await db.execute(query)

    html_rows = []
    for r in res.rows:
        inc_id = r[0]
        start = r[1]
        end = r[2]
        peak = r[3]

        status_chip = (
            '<button class="chip error">Đang diễn ra</button>'
            if end == "Active"
            else '<button class="chip outline">Xong</button>'
        )

        row = f"""
        <tr>
            <td>#{inc_id}</td>
            <td>{start}</td>
            <td>{end}</td>
            <td>{peak}°C</td>
            <td>{status_chip}</td>
        </tr>
        """
        html_rows.append(row)

    if not html_rows:
        return HTMLResponse(content="<tr><td colspan='5'>Chưa có nhật ký</td></tr>")

    return HTMLResponse(content="".join(html_rows))
