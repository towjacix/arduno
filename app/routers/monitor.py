import datetime

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

import app.database as database
from app.config import CONFIG
from app.schemas import MonitorPayload

__all__ = ["router"]

router = APIRouter()


async def get_dynamic_threshold() -> int:
    """Tính toán ngưỡng động với Type Safety và bộ lọc loại bỏ giá trị cháy."""
    if CONFIG["mode"] != "auto" or database.db is None:
        return int(CONFIG.get_nested("threshold", "default", "temp", default=45))

    window = int(CONFIG["window_size"])
    offset = int(CONFIG["temp_offset"])

    query = (
        "SELECT AVG(temp) FROM ("
        "SELECT temp FROM burning_logs "
        "WHERE temp < (SELECT current_dynamic_threshold FROM system_state WHERE id=1) "
        "ORDER BY id DESC LIMIT ?"
        ")"
    )
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

    # FIX: Áp dụng Hysteresis để tránh dao động trạng thái khi nhiệt độ ở sát ngưỡng.
    # Vào "critical": khi VƯỢT ngưỡng.
    # Ra "safe": chỉ khi THẤP HƠN (ngưỡng - hysteresis) — tránh flicker.
    hysteresis = int(CONFIG.get_nested("hysteresis", default=2))
    if old_status == "safe":
        new_status = (
            "critical"
            if data.temp > threshold or data.smoke > smoke_limit
            else "safe"
        )
    else:
        new_status = (
            "safe"
            if data.temp <= (threshold - hysteresis) and data.smoke <= smoke_limit
            else "critical"
        )

    # ID sự cố active — sẽ được cập nhật bên dưới nếu đang ở trạng thái critical
    active_inc_id: int = 0

    # LOGIC XỬ LÝ SỰ CỐ & CẬP NHẬT PEAK TEMP LIÊN TỤC
    if new_status == "critical":
        if old_status == "safe":
            # Tạo sự cố mới khi bắt đầu chuyển sang critical
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

            # FIX: Gán active_inc_id từ dữ liệu thực tế, không giữ nguyên 0
            inc_id = int(raw_inc_id) if isinstance(raw_inc_id, int) else 0
            active_inc_id = inc_id

            old_peak = float(raw_peak) if isinstance(raw_peak, (int, float)) else 0.0

            if data.temp > old_peak:
                # FIX: Dùng inc_id (đã resolve) thay vì active_inc_id = 0
                await database.db.execute(
                    "UPDATE incidents SET peak_temp = ? WHERE incident_id = ?",
                    [data.temp, inc_id],
                )

    elif old_status == "critical" and new_status == "safe":
        await database.db.execute(
            "UPDATE incidents SET end_time = ? WHERE end_time = 'Active'", [now]
        )

    # Ghi log liên tục vào burning_logs — FIX: dùng active_inc_id đã được cập nhật đúng
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
    """Gộp 3 thẻ chỉ số thành 1 cấu trúc Grid duy nhất để tối ưu hóa request."""
    if database.db is None:
        return HTMLResponse("<div id='status-grid'>Offline</div>")

    res = await database.db.execute(
        "SELECT status, temp, smoke, current_dynamic_threshold, timestamp "
        "FROM system_state WHERE id=1"
    )
    if not res.rows:
        return HTMLResponse("<div id='status-grid'>No data</div>")

    r = res.rows[0]
    raw_status, raw_temp, raw_smoke, raw_thresh, raw_ts = r[0], r[1], r[2], r[3], r[4]

    status = str(raw_status) if raw_status is not None else "safe"
    ts = str(raw_ts) if raw_ts is not None else ""

    temp = 0.0
    if isinstance(raw_temp, (int, float)):
        temp = float(raw_temp)

    smoke = 0
    if isinstance(raw_smoke, int):
        smoke = int(raw_smoke)

    thresh = 45
    if isinstance(raw_thresh, int):
        thresh = int(raw_thresh)

    status_class = "error" if status == "critical" else ""
    icon_class = "" if status == "critical" else "blue-text"
    status_icon = "warning" if status == "critical" else "check_circle"

    return HTMLResponse(
        content=f"""
    <div class="grid" id="status-grid" hx-get="/api/status" hx-trigger="every 2s" hx-swap="outerHTML">
        <div class="s12 m4">
            <article class="border round padding" style="margin: 0; min-height: 105px; height: 100%;">
                <div class="row">
                    <i class="orange-text">thermostat</i>
                    <div class="max">
                        <h6>Nhiệt độ</h6>
                        <h4>{temp:.1f}°C</h4>
                    </div>
                </div>
            </article>
        </div>

        <div class="s12 m4">
            <article class="border round padding" style="margin: 0; min-height: 105px; height: 100%;">
                <div class="row">
                    <i class="grey-text">cloud</i>
                    <div class="max">
                        <h6>Mật độ khói</h6>
                        <!-- FIX: Đổi nhãn từ PPM (sai) sang ADC (đúng với MQ-2 raw output) -->
                        <h4>{smoke} ADC</h4>
                    </div>
                </div>
            </article>
        </div>

        <div class="s12 m4">
            <article class="border round padding {status_class}" style="margin: 0; min-height: 105px; height: 100%;">
                <div class="row">
                    <i class="{icon_class}">{status_icon}</i>
                    <div class="max">
                        <h6>Hệ thống: {status.upper()}</h6>
                        <h4>{thresh}°C</h4>
                    </div>
                </div>
            </article>
        </div>
    </div>
    """
    )


@router.get("/api/history", response_class=HTMLResponse)
async def get_history_html(page: int = 1):
    """Lấy lịch sử cảnh báo phân trang bóc tách trực tiếp trên RAM Python."""
    if database.db is None:
        return HTMLResponse("")

    page = max(1, page)
    PAGE_SIZE = 5

    # Truy vấn với LIMIT bảo vệ để tránh quét toàn bảng khi dữ liệu lớn
    MAX_ROWS = 500
    query = (
        f"SELECT incident_id, start_time, end_time, peak_temp "
        f"FROM incidents ORDER BY incident_id DESC LIMIT {MAX_ROWS}"
    )
    res = await database.db.execute(query)

    total_items = len(res.rows)
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)

    offset = (page - 1) * PAGE_SIZE
    paginated_rows = res.rows[offset : offset + PAGE_SIZE]

    rows = []
    for r in paginated_rows:
        inc_id_raw, start_time_raw, end_time_raw, peak_temp_raw = r[0], r[1], r[2], r[3]

        inc_id = int(inc_id_raw) if isinstance(inc_id_raw, int) else 0
        start_time = str(start_time_raw) if start_time_raw is not None else ""
        dest_time = str(end_time_raw) if end_time_raw is not None else ""

        peak_temp = 0.0
        if isinstance(peak_temp_raw, (int, float)):
            peak_temp = float(peak_temp_raw)

        btn_class = "error" if dest_time == "Active" else "outline"
        btn_label = "LIVE" if dest_time == "Active" else "DONE"

        row = (
            f"<tr style='cursor: pointer;' "
            f"hx-get='/api/analytics/graph/{inc_id}' "
            f"hx-target='.graph-wrapper' "
            f"hx-swap='outerHTML'>"
            f"<td>#{inc_id}</td><td>{start_time}</td><td>{dest_time}</td>"
            f"<td>{peak_temp}°C</td><td>"
            f"<button class='chip {btn_class}'>{btn_label}</button></td></tr>"
        )
        rows.append(row)

    tbody_content = (
        "".join(rows)
        if rows
        else "<tr><td colspan='5' class='center-align'>Chưa có nhật ký</td></tr>"
    )

    tbody_html = (
        f'<tbody id="history-body" hx-get="/api/history?page={page}" '
        f'hx-trigger="every 10s" hx-swap="outerHTML">'
        f"{tbody_content}"
        f"</tbody>"
    )

    pag_buttons = []
    if page > 1:
        pag_buttons.append(
            f'<button class="chip outline" hx-get="/api/history?page={page - 1}" hx-target="#history-body" hx-swap="outerHTML"><i>chevron_left</i></button>'
        )

    for p in range(1, total_pages + 1):
        btn_class = "primary" if p == page else "outline"
        pag_buttons.append(
            f'<button class="chip {btn_class}" hx-get="/api/history?page={p}" hx-target="#history-body" hx-swap="outerHTML">{p}</button>'
        )

    if page < total_pages:
        pag_buttons.append(
            f'<button class="chip outline" hx-get="/api/history?page={page + 1}" hx-target="#history-body" hx-swap="outerHTML"><i>chevron_right</i></button>'
        )

    pag_html = (
        f'<div id="history-pagination" hx-swap-oob="true" '
        f'class="row center-align padding" style="justify-content: center; gap: 6px;">'
        f"{''.join(pag_buttons)}"
        f"</div>"
    )

    return HTMLResponse(content=f"{tbody_html}{pag_html}")
