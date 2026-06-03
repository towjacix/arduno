from typing import cast

from fastapi import APIRouter, Response

import app.database as database


__all__ = ["router"]

router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str, zoom: str = "flat"):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    # FIX: Xử lý lỗi nếu incident_id không phải số nguyên hợp lệ
    target_id: int = 0
    is_latest = incident_id == "latest"
    current_status = "safe"
    is_active: bool = False

    if not is_latest:
        try:
            target_id = int(incident_id)
        except ValueError:
            return Response(
                content="<div class='padding center-align'>ID sự cố không hợp lệ.</div>",
                media_type="text/html",
                status_code=400,
            )

    # 1. KIỂM TRA TRẠNG THÁI HỆ THỐNG HIỆN TẠI (CHỈ KHI XEM LIVE)
    if is_latest:
        state_res = await database.db.execute(
            "SELECT status FROM system_state WHERE id = 1"
        )
        if state_res.rows:
            current_status = str(state_res.rows[0][0])

    # 2. XÁC ĐỊNH DỮ LIỆU CẦN VẼ
    if is_latest:
        if current_status == "critical":
            id_q = (
                "SELECT incident_id FROM incidents "
                "WHERE end_time = 'Active' "
                "ORDER BY incident_id DESC LIMIT 1"
            )
            id_res = await database.db.execute(id_q)
            if id_res.rows:
                target_id = int(cast(int, id_res.rows[0][0]))
                q = (
                    "SELECT temp, smoke, timestamp FROM burning_logs "
                    "WHERE incident_id = ? ORDER BY id ASC"
                )
                res = await database.db.execute(q, [target_id])
            else:
                res = await database.db.execute(
                    "SELECT temp, smoke, timestamp FROM ("
                    "  SELECT temp, smoke, timestamp FROM burning_logs "
                    "  ORDER BY id DESC LIMIT 30"
                    ") AS recent ORDER BY timestamp ASC"
                )
        else:
            target_id = 0
            q = (
                "SELECT temp, smoke, timestamp FROM ("
                "  SELECT temp, smoke, timestamp FROM burning_logs "
                "  WHERE incident_id = 0 "
                "  ORDER BY id DESC LIMIT 30"
                ") AS recent ORDER BY timestamp ASC"
            )
            res = await database.db.execute(q)
    else:
        q = (
            "SELECT temp, smoke, timestamp FROM burning_logs "
            "WHERE incident_id = ? ORDER BY id ASC"
        )
        res = await database.db.execute(q, [target_id])

        id_res = await database.db.execute(
            "SELECT end_time FROM incidents WHERE incident_id = ?", [target_id]
        )
        if id_res.rows:
            is_active = str(id_res.rows[0][0]) == "Active"

    # 3. ÉP KIỂU VÀ CHUẨN HÓA DỮ LIỆU
    points_t: list[float] = []
    points_s: list[float] = []
    times: list[str] = []

    for row in res.rows:
        t_val = row[0]
        s_val = row[1]
        ts = row[2]
        if isinstance(t_val, (int, float)) and isinstance(s_val, (int, float)):
            points_t.append(float(t_val))
            points_s.append(float(s_val))
            ts_str = str(ts) if ts is not None else ""
            time_part = ts_str.split(" ")[1] if " " in ts_str else ts_str
            times.append(time_part)

    if not points_t:
        err_msg = "<p class='padding center-align'>Chưa có dữ liệu đo đạc.</p>"
        back_btn = ""
        if not is_latest:
            back_btn = (
                '<div class="center-align">'
                '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
                'hx-target=".graph-wrapper" hx-swap="outerHTML">'
                "<i>sensors</i> Trở về Giám Sát Phòng (Ambient)</button></div>"
            )

        if is_latest:
            htmx_attrs = f'hx-get="/api/analytics/graph/latest?zoom={zoom}" hx-trigger="every 5s" hx-swap="outerHTML"'
        elif is_active:
            htmx_attrs = f'hx-get="/api/analytics/graph/{target_id}?zoom={zoom}" hx-trigger="every 5s" hx-swap="outerHTML"'
        else:
            htmx_attrs = 'hx-swap="outerHTML"'

        err_div = (
            f'<div class="graph-wrapper margin" {htmx_attrs}>{err_msg}{back_btn}</div>'
        )
        return Response(content=err_div, media_type="text/html")

    # 4. THIẾT LẬP THÔNG SỐ TOẠ ĐỘ VÀ PADDING
    w, h = 800, 200
    p_left, p_right, p_top, p_bottom = 60, 60, 20, 30
    chart_w = w - p_left - p_right
    chart_h = h - p_top - p_bottom
    y_min = h - p_bottom

    # --- TOÁN HỌC TRỤC TRÁI (NHIỆT ĐỘ) ---
    min_t, max_t = min(points_t), max(points_t)

    MIN_SPAN = 2.0 if zoom == "detail" else 40.0
    current_span = max_t - min_t
    if current_span < MIN_SPAN:
        diff = MIN_SPAN - current_span
        min_t = max(20.0, min_t - diff / 2.0)
        max_t = min_t + MIN_SPAN
    span_t = max_t - min_t

    # --- TOÁN HỌC TRỤC PHẢI (KHÓI) ---
    max_s = max(points_s) if points_s else 300.0
    smoke_ceiling = max(400.0, float(max_s))
    span_s = smoke_ceiling

    # Tính toán tọa độ đường trendline Nhiệt độ
    coords_t: list[str] = []
    num_pts = len(points_t)
    for i, v in enumerate(points_t):
        x = p_left + (i / (num_pts - 1)) * chart_w if num_pts > 1 else p_left
        y = y_min - ((v - min_t) / span_t * chart_h)
        coords_t.append(f"{x},{y}")

    points_str_t = " ".join(coords_t)
    x_last = p_left + chart_w if num_pts > 1 else p_left

    # 5. KHỞI TẠO TỌA ĐỘ CHO 3 ĐIỂM DỮ LIỆU CHÍNH
    # FIX: Xoá biến mid_idx không dùng đến
    t_start = points_t[0]
    time_start = times[0]
    y_start_t = y_min - ((t_start - min_t) / span_t * chart_h)

    peak_idx = points_t.index(max(points_t))
    t_peak = points_t[peak_idx]
    time_peak = times[peak_idx]
    x_peak = p_left + (peak_idx / (num_pts - 1)) * chart_w if num_pts > 1 else p_left
    y_peak_t = y_min - ((t_peak - min_t) / span_t * chart_h)

    t_end = points_t[-1]
    time_end = times[-1]
    y_end_t = y_min - ((t_end - min_t) / span_t * chart_h)

    # 6. DỰNG CỘT KHÓI SVG
    columns_list: list[str] = []
    col_w = (chart_w / num_pts) * 0.4 if num_pts > 1 else 15.0
    for i, s in enumerate(points_s):
        x = p_left + (i / (num_pts - 1)) * chart_w if num_pts > 1 else p_left
        col_h = (s / span_s) * chart_h
        col_x = x - col_w / 2.0
        col_y = y_min - col_h
        rect = (
            f'<rect x="{col_x}" y="{col_y}" width="{col_w}" height="{col_h}" '
            f'fill="rgba(33, 150, 243, 0.12)" stroke="#2196f3" stroke-width="0.8" />'
        )
        columns_list.append(rect)
    columns_svg = "".join(columns_list)

    # 7. KHAI BÁO HẰNG SỐ MÀU SẮC
    axis_color = "#455a64"
    text_color = "#9e9e9e"
    grid_color = "rgba(255, 255, 255, 0.22)"

    proj_lines = (
        f'<line x1="{p_left}" y1="{y_peak_t}" x2="{w - p_right}" y2="{y_peak_t}" stroke="{grid_color}" stroke-dasharray="4" />'
        f'<line x1="{x_peak}" y1="{y_min}" x2="{x_peak}" y2="{p_top}" stroke="{grid_color}" stroke-dasharray="4" />'
        f'<line x1="{p_left}" y1="{y_end_t}" x2="{w - p_right}" y2="{y_end_t}" stroke="{grid_color}" stroke-dasharray="4" />'
        f'<line x1="{x_last}" y1="{y_min}" x2="{x_last}" y2="{p_top}" stroke="{grid_color}" stroke-dasharray="4" />'
    )

    axes = (
        f'<line x1="{p_left}" y1="{p_top}" x2="{p_left}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'
        f'<line x1="{w - p_right}" y1="{p_top}" x2="{w - p_right}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'
        f'<line x1="{p_left}" y1="{y_min}" x2="{w - p_right}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'
    )

    MIN_LABEL_GAP = 20.0
    y_labels_temp_list = [
        f'<text x="{p_left - 10}" y="{y_start_t + 4}" text-anchor="end" fill="#ff5722" font-size="11">{t_start:.1f}°C</text>'
    ]
    if abs(y_peak_t - y_start_t) >= MIN_LABEL_GAP:
        y_labels_temp_list.append(
            f'<text x="{p_left - 10}" y="{y_peak_t + 4}" text-anchor="end" fill="#ff5722" font-size="11">{t_peak:.1f}°C</text>'
        )
    if (
        abs(y_end_t - y_start_t) >= MIN_LABEL_GAP
        and abs(y_end_t - y_peak_t) >= MIN_LABEL_GAP
    ):
        y_labels_temp_list.append(
            f'<text x="{p_left - 10}" y="{y_end_t + 4}" text-anchor="end" fill="#ff5722" font-size="11">{t_end:.1f}°C</text>'
        )
    y_labels_temp = "".join(y_labels_temp_list)

    # FIX: Đổi nhãn "PPM" thành "ADC" cho đúng với output thực của MQ-2
    y_mid = p_top + chart_h / 2.0
    y_labels_smoke = (
        f'<text x="{w - p_right + 10}" y="{p_top + 4}" text-anchor="start" fill="#2196f3" font-size="11">{smoke_ceiling:.0f} ADC</text>'
        f'<text x="{w - p_right + 10}" y="{y_mid + 4}" text-anchor="start" fill="#2196f3" font-size="11">{(smoke_ceiling / 2):.0f} ADC</text>'
        f'<text x="{w - p_right + 10}" y="{y_min + 4}" text-anchor="start" fill="#2196f3" font-size="11">0 ADC</text>'
    )

    x_labels_list = [
        f'<text x="{p_left}" y="{h - 10}" text-anchor="start" fill="{text_color}" font-size="11">{time_start}</text>'
    ]
    MIN_X_GAP = 80.0
    if abs(x_peak - p_left) >= MIN_X_GAP and abs(x_last - x_peak) >= MIN_X_GAP:
        x_labels_list.append(
            f'<text x="{x_peak}" y="{h - 10}" text-anchor="middle" fill="{text_color}" font-size="11">{time_peak}</text>'
        )
    x_labels_list.append(
        f'<text x="{x_last}" y="{h - 10}" text-anchor="end" fill="{text_color}" font-size="11">{time_end}</text>'
    )
    x_labels = "".join(x_labels_list)

    data_nodes = (
        f'<circle cx="{p_left}" cy="{y_start_t}" r="3" fill="#ff3d00" />'
        f'<circle cx="{x_peak}" cy="{y_peak_t}" r="3" fill="#ff3d00" />'
        f'<circle cx="{x_last}" cy="{y_end_t}" r="2.5" fill="#ff3d00" />'
    )

    # 8. DỰNG HÌNH VECTOR SVG
    svg_path = (
        f'<path d="M {p_left},{y_min} L {points_str_t} L {x_last},{y_min} Z" '
        'fill="rgba(255,61,0,0.08)"/>'
    )
    svg_line = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="1.8" '
        f'stroke-linecap="round" points="{points_str_t}" />'
    )

    svg_graphics = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:{h}px; overflow:visible; display:block;">'
        f"{axes}{columns_svg}{svg_path}{proj_lines}{y_labels_temp}{y_labels_smoke}{x_labels}{svg_line}{data_nodes}</svg>"
    )

    # 9. THANH ĐIỀU HƯỚNG ZOOM
    class_flat = "primary" if zoom == "flat" else "outline"
    class_detail = "primary" if zoom == "detail" else "outline"
    zoom_bar = (
        f'<div class="row" style="gap: 6px; justify-content: flex-end; margin-bottom: 8px;">'
        f'<button class="chip {class_flat}" hx-get="/api/analytics/graph/{incident_id}?zoom=flat" hx-target=".graph-wrapper" hx-swap="outerHTML">Mặc định</button>'
        f'<button class="chip {class_detail}" hx-get="/api/analytics/graph/{incident_id}?zoom=detail" hx-target=".graph-wrapper" hx-swap="outerHTML">Phóng to (Zoom)</button>'
        f"</div>"
    )

    # 10. TRẢ VỀ HTML
    if is_latest:
        wrapper = (
            f'<div class="graph-wrapper margin" '
            f'hx-get="/api/analytics/graph/latest?zoom={zoom}" '
            f'hx-trigger="every 5s" hx-swap="outerHTML">'
            f"{zoom_bar}{svg_graphics}</div>"
        )
    elif is_active:
        status_bar = (
            '<div class="row margin valign" style="gap: 12px; margin-bottom: 16px;">'
            f'<span class="chip error padding blinking">Sự cố #{target_id} ĐANG DIỄN RA (LIVE)</span>'
            '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
            'hx-target=".graph-wrapper" hx-swap="outerHTML">'
            "<i>sensors</i> Trở về Giám Sát Phòng (Ambient)</button></div>"
        )
        wrapper = (
            f'<div class="graph-wrapper margin" '
            f'hx-get="/api/analytics/graph/{target_id}?zoom={zoom}" '
            f'hx-trigger="every 5s" hx-swap="outerHTML">'
            f"{status_bar}{zoom_bar}{svg_graphics}</div>"
        )
    else:
        status_bar = (
            '<div class="row margin valign" style="gap: 12px; margin-bottom: 16px;">'
            f'<span class="chip error padding">Lịch sử sự cố #{target_id}</span>'
            '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
            'hx-target=".graph-wrapper" hx-swap="outerHTML">'
            "<i>sensors</i> Trở về Giám Sát Phòng (Ambient)</button></div>"
        )
        wrapper = (
            '<div class="graph-wrapper margin" hx-swap="outerHTML">'
            f"{status_bar}{zoom_bar}{svg_graphics}</div>"
        )

    return Response(content=wrapper, media_type="text/html")
