from typing import cast

from fastapi import APIRouter, Response

import app.database as database


router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    # KHAI BÁO MẶC ĐỊNH TRÊN ĐẦU HÀM ĐỂ TRÁNH LỖI PHẠM VI BIẾN (reportUndefinedVariable)
    target_id: int = 0
    is_latest = incident_id == "latest"
    current_status = "safe"
    is_active: bool = False

    # 1. KIỂM TRA TRẠNG THÁI HỆ THỐNG HIỆN TẠI (CHỈ KHI XEM LIVE)
    if is_latest:
        state_res = await database.db.execute(
            "SELECT status FROM system_state WHERE id = 1"
        )
        if state_res.rows:
            current_status = str(state_res.rows[0][0])

    # 2. XÁC ĐỊNH DỮ LIỆU CẦN VẼ (REAL-TIME AMBIENT VS INCIDENTS)
    if is_latest:
        if current_status == "critical":
            # ĐANG CHÁY: Vẽ diễn biến sự cố Active thời gian thực
            id_q = (
                "SELECT incident_id FROM incidents "
                "WHERE end_time = 'Active' "
                "ORDER BY incident_id DESC LIMIT 1"
            )
            id_res = await database.db.execute(id_q)
            if id_res.rows:
                target_id = int(cast(int, id_res.rows[0][0]))
                q = (
                    "SELECT temp, timestamp FROM burning_logs "
                    "WHERE incident_id = ? ORDER BY timestamp ASC"
                )
                res = await database.db.execute(q, [target_id])
            else:
                res = await database.db.execute(
                    "SELECT temp, timestamp FROM ("
                    "  SELECT temp, timestamp FROM burning_logs "
                    "  ORDER BY id DESC LIMIT 30"
                    ") ORDER BY timestamp ASC"
                )
        else:
            # ĐANG AN TOÀN: Vẽ biểu đồ giám sát môi trường phòng Lab thời gian thực (incident_id = 0)
            target_id = 0
            q = (
                "SELECT temp, timestamp FROM ("
                "  SELECT temp, timestamp FROM burning_logs "
                "  WHERE incident_id = 0 "
                "  ORDER BY id DESC LIMIT 30"
                ") ORDER BY timestamp ASC"
            )
            res = await database.db.execute(q)
    else:
        # XEM LỊCH SỬ TĨNH: Vẽ sự cố cụ thể đã chọn
        target_id = int(incident_id)
        q = (
            "SELECT temp, timestamp FROM burning_logs "
            "WHERE incident_id = ? ORDER BY timestamp ASC"
        )
        res = await database.db.execute(q, [target_id])

        # Kiểm tra xem sự cố cụ thể này có đang Active hay không
        id_res = await database.db.execute(
            "SELECT end_time FROM incidents WHERE incident_id = ?", [target_id]
        )
        if id_res.rows:
            is_active = str(id_res.rows[0][0]) == "Active"

    # 3. ÉP KIỂU VÀ CHUẨN HÓA DỮ LIỆU (Type Safety cho Basedpyright)
    points: list[float] = []
    times: list[str] = []

    for row in res.rows:
        val = row[0]
        ts = row[1]
        if isinstance(val, (int, float)):
            points.append(float(val))
            ts_str = str(ts) if ts is not None else ""
            time_part = ts_str.split(" ")[1] if " " in ts_str else ts_str
            times.append(time_part)

    if not points:
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
    p_left, p_right, p_top, p_bottom = 60, 20, 20, 30
    chart_w = w - p_left - p_right
    chart_h = h - p_top - p_bottom

    min_t, max_t = min(points), max(points)

    # [NÂNG CẤP: THUẬT TOÁN CO GIÃN ĐỒ THỊ CHỦ ĐỘNG]
    # detail: dải đo hẹp 5°C để soi rõ biến động nhỏ. flat: dải đo rộng 40°C để làm phẳng.
    MIN_SPAN = 5.0 if zoom == "detail" else 40.0
    current_span = max_t - min_t
    if current_span < MIN_SPAN:
        diff = MIN_SPAN - current_span
        min_t = max(20.0, min_t - diff / 2.0)
        max_t = min_t + MIN_SPAN

    span = max_t - min_t

    # Tính toán tọa độ của đường trendline
    coords: list[str] = []
    num_pts = len(points)
    for i, v in enumerate(points):
        x = p_left + (i / (num_pts - 1)) * chart_w if num_pts > 1 else p_left
        y = (h - p_bottom) - ((v - min_t) / span * chart_h)
        coords.append(f"{x},{y}")

    points_str = " ".join(coords)
    x_last = p_left + chart_w if num_pts > 1 else p_left

    # 5. KHỞI TẠO TỌA ĐỘ CHO 3 ĐIỂM DỮ LIỆU CHÍNH
    mid_idx = num_pts // 2
    t_start = points[0]
    time_start = times[0]
    y_start = (h - p_bottom) - ((t_start - min_t) / span * chart_h)

    t_mid = points[mid_idx]
    time_mid = times[mid_idx]
    x_mid = p_left + (mid_idx / (num_pts - 1)) * chart_w if num_pts > 1 else p_left
    y_mid = (h - p_bottom) - ((t_mid - min_t) / span * chart_h)

    t_end = points[-1]
    time_end = times[-1]
    y_end = (h - p_bottom) - ((t_end - min_t) / span * chart_h)

    # 6. THUẬT TOÁN CHỐNG ĐÈ CHỮ THÔNG MINH
    axis_color = "#455a64"
    text_color = "#9e9e9e"
    grid_color = "rgba(255, 255, 255, 0.22)"
    y_min = h - p_bottom

    MIN_LABEL_GAP = 20.0

    y_labels_list = []
    proj_lines_list = []
    data_nodes_list = []

    # Điểm 1 (Bắt đầu)
    y_labels_list.append(
        f'<text x="{p_left - 10}" y="{y_start + 4}" text-anchor="end" fill="{text_color}" font-size="11">{t_start:.1f}°C</text>'
    )
    data_nodes_list.append(
        f'<circle cx="{p_left}" cy="{y_start}" r="3" fill="#ff3d00" />'
    )

    # Điểm 2 (Giữa)
    if abs(y_mid - y_start) >= MIN_LABEL_GAP:
        y_labels_list.append(
            f'<text x="{p_left - 10}" y="{y_mid + 4}" text-anchor="end" fill="{text_color}" font-size="11">{t_mid:.1f}°C</text>'
        )
        proj_lines_list.append(
            f'<line x1="{p_left}" y1="{y_mid}" x2="{x_mid}" y2="{y_mid}" stroke="{grid_color}" stroke-dasharray="4" />'
        )

    proj_lines_list.append(
        f'<line x1="{x_mid}" y1="{y_min}" x2="{x_mid}" y2="{y_mid}" stroke="{grid_color}" stroke-dasharray="4" />'
    )
    data_nodes_list.append(
        f'<circle cx="{x_mid}" cy="{y_mid}" r="2.5" fill="#ff3d00" />'
    )

    # Điểm 3 (Kết thúc)
    if abs(y_end - y_start) >= MIN_LABEL_GAP and abs(y_end - y_mid) >= MIN_LABEL_GAP:
        y_labels_list.append(
            f'<text x="{p_left - 10}" y="{y_end + 4}" text-anchor="end" fill="{text_color}" font-size="11">{t_end:.1f}°C</text>'
        )
        proj_lines_list.append(
            f'<line x1="{p_left}" y1="{y_end}" x2="{x_last}" y2="{y_end}" stroke="{grid_color}" stroke-dasharray="4" />'
        )

    proj_lines_list.append(
        f'<line x1="{x_last}" y1="{y_min}" x2="{x_last}" y2="{y_end}" stroke="{grid_color}" stroke-dasharray="4" />'
    )
    data_nodes_list.append(
        f'<circle cx="{x_last}" cy="{y_end}" r="2.5" fill="#ff3d00" />'
    )

    y_labels = "".join(y_labels_list)
    proj_lines = "".join(proj_lines_list)
    data_nodes = "".join(data_nodes_list)

    # Trục chính và nhãn
    axes = (
        f'<line x1="{p_left}" y1="{p_top}" x2="{p_left}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'
        f'<line x1="{p_left}" y1="{y_min}" x2="{w - p_right}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'
    )

    x_labels = (
        f'<text x="{p_left}" y="{h - 10}" text-anchor="start" fill="{text_color}" font-size="11">{time_start}</text>'
        f'<text x="{x_mid}" y="{h - 10}" text-anchor="middle" fill="{text_color}" font-size="11">{time_mid}</text>'
        f'<text x="{x_last}" y="{h - 10}" text-anchor="end" fill="{text_color}" font-size="11">{time_end}</text>'
    )

    # 7. DỰNG HÌNH VECTOR (SVG) - Sửa đổi thứ tự vẽ: vẽ svg_path trước để tránh che mờ nét đứt
    svg_path = (
        f'<path d="M {p_left},{y_min} L {points_str} L {x_last},{y_min} Z" '
        'fill="rgba(255,61,0,0.1)"/>'
    )
    svg_line = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="1.8" '
        f'stroke-linecap="round" points="{points_str}" />'
    )

    svg_graphics = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:{h}px; overflow:visible; display:block;">'
        f"{axes}{svg_path}{proj_lines}{y_labels}{x_labels}{svg_line}{data_nodes}</svg>"
    )

    # 8. THANH ĐIỀU HƯỚNG ZOOM ĐỘNG (Bằng Beer CSS & HTMX)
    class_flat = "primary" if zoom == "flat" else "outline"
    class_detail = "primary" if zoom == "detail" else "outline"
    zoom_bar = (
        f'<div class="row" style="gap: 6px; justify-content: flex-end; margin-bottom: 8px;">'
        f'<button class="chip {class_flat}" hx-get="/api/analytics/graph/{incident_id}?zoom=flat" hx-target=".graph-wrapper" hx-swap="outerHTML">Mặc định</button>'
        f'<button class="chip {class_detail}" hx-get="/api/analytics/graph/{incident_id}?zoom=detail" hx-target=".graph-wrapper" hx-swap="outerHTML">Phóng to (Zoom)</button>'
        f"</div>"
    )

    # 9. TRẢ VỀ TRẠNG THÁI KIỂM SOÁT TRIGGER QUA HTML (HATEOAS)
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
            f'<div class="graph-wrapper margin" hx-swap="outerHTML">'
            f"{status_bar}{zoom_bar}{svg_graphics}</div>"
        )

    return Response(content=wrapper, media_type="text/html")
