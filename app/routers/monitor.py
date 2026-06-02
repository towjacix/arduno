import datetime

from fastapi import APIRouter, Response

import app.database as database
from app.config import CONFIG
from app.schemas import MonitorPayload


router = APIRouter()


async def get_dynamic_threshold() -> int:
    if CONFIG["mode"] != "auto" or database.db is None:
        return int(CONFIG.get_nested("threshold", "default", "temp", default=45))

    window = int(CONFIG["window_size"])
    offset = int(CONFIG["temp_offset"])

    # BỘ LỌC THÔNG MINH: Chỉ lấy trung bình của các mẫu "không cháy"
    # Lọc bỏ những mẫu có nhiệt độ cao bất thường (> ngưỡng hiện tại)
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

    # Lấy dữ liệu cũ
    state_res = await database.db.execute(
        "SELECT status FROM system_state WHERE id = 1"
    )
    old_status = str(state_res.rows[0][0]) if state_res.rows else "safe"

    threshold = await get_dynamic_threshold()
    smoke_limit = int(CONFIG.get_nested("threshold", "default", "smoke", default=300))

    new_status = (
        "critical" if data.temp > threshold or data.smoke > smoke_limit else "safe"
    )

    # LOGIC INCIDENT VÀ PEAK TEMP
    if new_status == "critical":
        if old_status == "safe":
            await database.db.execute(
                "INSERT INTO incidents (start_time, end_time, peak_temp) VALUES (?, 'Active', ?)",
                [now, data.temp],
            )

        # Cập nhật Peak Temp chuẩn xác
        inc_res = await database.db.execute(
            "SELECT incident_id, peak_temp FROM incidents WHERE end_time = 'Active' LIMIT 1"
        )
        if inc_res.rows:
            inc_id, old_peak = int(inc_res.rows[0][0]), float(inc_res.rows[0][1])
            if data.temp > old_peak:
                await database.db.execute(
                    "UPDATE incidents SET peak_temp = ? WHERE incident_id = ?",
                    [data.temp, inc_id],
                )

            await database.db.execute(
                "INSERT INTO burning_logs (incident_id, timestamp, temp, smoke) VALUES (?, ?, ?, ?)",
                [inc_id, now, data.temp, data.smoke],
            )

    elif old_status == "critical" and new_status == "safe":
        await database.db.execute(
            "UPDATE incidents SET end_time = ? WHERE end_time = 'Active'", [now]
        )

    await database.db.execute(
        "UPDATE system_state SET status=?, timestamp=?, temp=?, smoke=?, current_dynamic_threshold=? WHERE id=1",
        [new_status, now, data.temp, data.smoke, threshold],
    )
    return {"status": new_status}


# (Các hàm get_status_html và get_history_html giữ nguyên)
