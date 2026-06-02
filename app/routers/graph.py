from typing import cast

from fastapi import APIRouter, Response

import app.database as database


router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    target_id: int = 0
    is_latest = incident_id == "latest"

    # 1. Xác định Incident ID cần kết xuất đồ họa
    if is_latest:
        id_q = "SELECT incident_id FROM incidents ORDER BY incident_id DESC LIMIT 1"
        id_res = await database.db.execute(id_q)
        if not id_res.rows:
            empty_div = (
                '<div class="graph-wrapper margin" '
                'hx-get="/api/analytics/graph/latest" '
                'hx-trigger="every 5s" hx-swap="outerHTML">'
                '<p class="center-align padding">Hệ thống đang hoạt động an toàn. '
                "Chưa có sự cố để vẽ đồ thị.</p></div>"
            )
            return Response(content=empty_div, media_type="text/html")
        target_id = int(cast(int, id_res.rows[0][0]))
    else:
        target_id = int(incident_id)

    # 2. Truy vấn dữ liệu nhiệt độ và mốc thời gian tương ứng
    q = (
        "SELECT temp, timestamp FROM burning_logs "
        "WHERE incident_id = ? ORDER BY timestamp ASC"
    )
    res = await database.db.execute(q, [target_id])

    # Ép kiểu và tách mảng an toàn (Thu hẹp kiểu dữ liệu cho Basedpyright)
    points: list[float] = []
    times: list[str] = []

    for row in res.rows:
        val = row[0]
        ts = row[1]
        if isinstance(val, (int, float)):
            points.append(float(val))
            # Rút gọn chuỗi timestamp YYYY-MM-DD HH:MM:SS về dạng HH:MM:SS
            ts_str = str(ts) if ts is not None else ""
            time_part = ts_str.split(" ")[1] if " " in ts_str else ts_str
            times.append(time_part)

    if not points:
        err_msg = (
            f"<p class='padding center-align'>Sự cố #{target_id} "
            "chưa thu thập dữ liệu nhiệt độ để vẽ đồ thị.</p>"
        )
        back_btn = ""
        if not is_latest:
            back_btn = (
                '<div class="center-align">'
                '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
                'hx-target=".graph-wrapper" hx-swap="outerHTML">'
                "Quay lại Xem Trực Tiếp</button></div>"
            )

        if is_latest:
            htmx_attrs = 'hx-get="/api/analytics/graph/latest" hx-trigger="every 5s" hx-swap="outerHTML"'
        else:
            htmx_attrs = 'hx-swap="outerHTML"'

        err_div = (
            f'<div class="graph-wrapper margin" {htmx_attrs}>{err_msg}{back_btn}</div>'
        )
        return Response(content=err_div, media_type="text/html")

    # 3. THIẾT LẬP THÔNG SỐ TOẠ ĐỘ VÀ PADDING (VÙNG AN TOÀN CHO TRỤC)
    w, h = 800, 200
    p_left, p_right, p_top, p_bottom = 60, 20, 20, 30
    chart_w = w - p_left - p_right
    chart_h = h - p_top - p_bottom

    min_t, max_t = min(points), max(points)

    # [THUẬT TOÁN LÀM PHẲNG ĐỒ THỊ]
    MIN_SPAN = 40.0
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

    # 4. THIẾT KẾ GRIDLINES VÀ NHÃN TRỤC Y (NHIỆT ĐỘ)
    axis_color = "#455a64"
    text_color = "#9e9e9e"  # Tăng độ sáng chữ nhãn từ #888888 lên #9e9e9e
    grid_color = "rgba(255, 255, 255, 0.22)"  # Tăng mạnh độ rõ nét từ 0.07 lên 0.22

    mid_t = (max_t + min_t) / 2.0
    y_max, y_min = p_top, h - p_bottom
    y_mid = p_top + chart_h / 2.0

    y_labels = (
        f'<text x="{p_left - 10}" y="{y_max + 4}" text-anchor="end" fill="{text_color}" font-size="11">{max_t:.1f}°C</text>'
        f'<text x="{p_left - 10}" y="{y_mid + 4}" text-anchor="end" fill="{text_color}" font-size="11">{mid_t:.1f}°C</text>'
        f'<text x="{p_left - 10}" y="{y_min + 4}" text-anchor="end" fill="{text_color}" font-size="11">{min_t:.1f}°C</text>'
    )

    # 5. THIẾT KẾ NHÃN TRỤC X VÀ ĐƯỜNG LƯỚI DỌC (TIME GRIDLINES)
    x_labels = ""
    vertical_grid = ""
    if len(times) >= 2:
        start_time = times[0]
        end_time = times[-1]
        mid_time = times[len(times) // 2]

        x_mid = p_left + chart_w / 2.0
        x_end = w - p_right

        x_labels = (
            f'<text x="{p_left}" y="{h - 10}" text-anchor="start" fill="{text_color}" font-size="11">{start_time}</text>'
            f'<text x="{x_mid}" y="{h - 10}" text-anchor="middle" fill="{text_color}" font-size="11">{mid_time}</text>'
            f'<text x="{x_end}" y="{h - 10}" text-anchor="end" fill="{text_color}" font-size="11">{end_time}</text>'
        )

        # Tạo lưới đứt nét dọc chỉ điểm mốc thời gian
        vertical_grid = (
            f'<line x1="{x_mid}" y1="{y_max}" x2="{x_mid}" y2="{y_min}" stroke="{grid_color}" stroke-dasharray="4" />'
            f'<line x1="{x_end}" y1="{y_max}" x2="{x_end}" y2="{y_min}" stroke="{grid_color}" stroke-dasharray="4" />'
        )

    grid_lines = (
        f'<line x1="{p_left}" y1="{y_max}" x2="{w - p_right}" y2="{y_max}" stroke="{grid_color}" stroke-dasharray="4" />'
        f'<line x1="{p_left}" y1="{y_mid}" x2="{w - p_right}" y2="{y_mid}" stroke="{grid_color}" stroke-dasharray="4" />'
        f'<line x1="{p_left}" y1="{y_min}" x2="{w - p_right}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'  # Trục hoành X
        f'<line x1="{p_left}" y1="{y_max}" x2="{p_left}" y2="{y_min}" stroke="{axis_color}" stroke-width="1.2" />'  # Trục tung Y
        f"{vertical_grid}"
    )

    # 6. DỰNG HÌNH VECTOR (SVG)
    svg_path = (
        f'<path d="M {p_left},{y_min} L {points_str} L {x_last},{y_min} Z" '
        'fill="rgba(255,61,0,0.1)"/>'
    )
    svg_line = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="1.8" '
        f'stroke-linecap="round" points="{points_str}" />'
    )

    # Kết hợp các thành phần đồ họa
    svg_graphics = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:{h}px; overflow:visible; display:block;">'
        f"{grid_lines}{y_labels}{x_labels}{svg_path}{svg_line}</svg>"
    )

    # 7. TRẢ VỀ TRẠNG THÁI KIỂM SOÁT TRIGGER QUA HTML (HATEOAS)
    if is_latest:
        wrapper = (
            '<div class="graph-wrapper margin" '
            'hx-get="/api/analytics/graph/latest" '
            'hx-trigger="every 5s" hx-swap="outerHTML">'
            f"{svg_graphics}</div>"
        )
    else:
        status_bar = (
            '<div class="row margin valign" style="gap: 12px; margin-bottom: 16px;">'
            f'<span class="chip error padding">Đang xem lịch sử vụ #{target_id}</span>'
            '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
            'hx-target=".graph-wrapper" hx-swap="outerHTML">'
            "<i>sensors</i> Xem Trực Tiếp (LIVE)</button></div>"
        )
        wrapper = (
            '<div class="graph-wrapper margin" hx-swap="outerHTML">'
            f"{status_bar}{svg_graphics}</div>"
        )

    return Response(content=wrapper, media_type="text/html")
