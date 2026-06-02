from typing import cast

from fastapi import APIRouter, Response

import app.database as database


router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str, zoom: str = "flat"):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    target_id: int = 0
    is_latest = incident_id == "latest"
    current_status = "safe"
    is_active = False

    # 1. KIỂM TRA TRẠNG THÁI HỆ THỐNG HIỆN TẠI (CHỈ KHI XEM LIVE)
    if is_latest:
        state_res = await database.db.execute(
            "SELECT status FROM system_state WHERE id = 1"
        )
        if state_res.rows:
            current_status = str(state_res.rows[0][0])

    # 2. XÁC ĐỊNH DỮ LIỆU CẦN VẼ (TEMP VÀ SMOKE)
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
                    "WHERE incident_id = ? ORDER BY timestamp ASC"
                )
                res = await database.db.execute(q, [target_id])
            else:
                res = await database.db.execute(
                    "SELECT temp, smoke, timestamp FROM ("
                    "  SELECT temp, smoke, timestamp FROM burning_logs "
                    "  ORDER BY id DESC LIMIT 30"
                    ") ORDER BY timestamp ASC"
                )
        else:
            target_id = 0
            q = (
                "SELECT temp, smoke, timestamp FROM ("
                "  SELECT temp, smoke, timestamp FROM burning_logs "
                "  WHERE incident_id = 0 "
                "  ORDER BY id DESC LIMIT 30"
                ") ORDER BY timestamp ASC"
            )
            res = await database.db.execute(q)
    else:
        target_id = int(incident_id)
        q = (
            "SELECT temp, smoke, timestamp FROM burning_logs "
            "WHERE incident_id = ? ORDER BY timestamp ASC"
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

    # ============================================================
    # TOÁN HỌC QUY ĐỔI HỆ TỌA ĐỘ VIRTUAL % (0 - 100) CHUẨN PANCAKE
    # ============================================================
    # Trục Y trái (Nhiệt độ)
    min_t, max_t = min(points_t), max(points_t)
    MIN_SPAN = 2.0 if zoom == "detail" else 40.0
    current_span = max_t - min_t
    if current_span < MIN_SPAN:
        diff = MIN_SPAN - current_span
        min_t = max(20.0, min_t - diff / 2.0)
        max_t = min_t + MIN_SPAN
    span_t = max_t - min_t

    # Trục Y phải (Khói)
    max_s = max(points_s) if points_s else 300.0
    smoke_ceiling = max(400.0, float(max_s))
    span_s = smoke_ceiling

    # Tính toán tọa độ ảo phần trăm (0 - 100) cho đường trendline Nhiệt độ
    coords_t: list[str] = []
    num_pts = len(points_t)
    for i, v in enumerate(points_t):
        x = (i / (num_pts - 1)) * 100.0 if num_pts > 1 else 0.0
        y = 100.0 - ((v - min_t) / span_t * 100.0)
        coords_t.append(f"{x},{y}")

    points_str_t = " ".join(coords_t)
    x_last_pct = 100.0 if num_pts > 1 else 0.0

    # Khởi tạo tọa độ % cho 3 điểm dữ liệu chính
    mid_idx = num_pts // 2
    t_start = points_t[0]
    time_start = times[0]
    y_start_pct = 100.0 - ((t_start - min_t) / span_t * 100.0)

    peak_idx = points_t.index(max(points_t))
    t_peak = points_t[peak_idx]
    time_peak = times[peak_idx]
    x_peak_pct = (peak_idx / (num_pts - 1)) * 100.0 if num_pts > 1 else 0.0
    y_peak_pct = 100.0 - ((t_peak - min_t) / span_t * 100.0)

    t_end = points_t[-1]
    time_end = times[-1]
    y_end_pct = 100.0 - ((t_end - min_t) / span_t * 100.0)

    # 4. DỰNG CỘT KHÓI SVG (TỌA ĐỘ %)
    columns_list: list[str] = []
    col_w_pct = (100.0 / num_pts) * 0.4 if num_pts > 1 else 5.0
    for i, s in enumerate(points_s):
        x_pct = (i / (num_pts - 1)) * 100.0 if num_pts > 1 else 0.0
        col_h_pct = (s / span_s) * 100.0
        col_x_pct = x_pct - col_w_pct / 2.0
        col_y_pct = 100.0 - col_h_pct
        rect = (
            f'<rect x="{col_x_pct}" y="{col_y_pct}" width="{col_w_pct}" height="{col_h_pct}" '
            f'fill="rgba(33, 150, 243, 0.12)" stroke="#2196f3" stroke-width="0.8" '
            f'vector-effect="non-scaling-stroke" />'
        )
        columns_list.append(rect)
    columns_svg = "".join(columns_list)

    # 5. ĐƯỜNG DÓNG ĐỨT NÉT CHỈ ĐIỂM SVG (TỌA ĐỘ %)
    grid_color = "rgba(255, 255, 255, 0.22)"
    proj_lines = (
        f'<line x1="0" y1="{y_peak_pct}" x2="{x_peak_pct}" y2="{y_peak_pct}" stroke="{grid_color}" stroke-dasharray="4" vector-effect="non-scaling-stroke" />'
        f'<line x1="{x_peak_pct}" y1="100" x2="{x_peak_pct}" y2="0" stroke="{grid_color}" stroke-dasharray="4" vector-effect="non-scaling-stroke" />'
        f'<line x1="0" y1="{y_end_pct}" x2="{x_last_pct}" y2="{y_end_pct}" stroke="{grid_color}" stroke-dasharray="4" vector-effect="non-scaling-stroke" />'
        f'<line x1="{x_last_pct}" y1="100" x2="{x_last_pct}" y2="0" stroke="{grid_color}" stroke-dasharray="4" vector-effect="non-scaling-stroke" />'
    )

    # Chấm tròn đỏ chỉ điểm dữ liệu chính
    data_nodes = (
        f'<circle cx="0" cy="{y_start_pct}" r="3.5" fill="#ff3d00" />'
        f'<circle cx="{x_peak_pct}" cy="{y_peak_pct}" r="3.5" fill="#ff3d00" />'
        f'<circle cx="{x_last_pct}" cy="{y_end_pct}" r="3.5" fill="#ff3d00" />'
    )

    # DỰNG HÌNH VECTOR TRƠN (Không chứa TEXT để tránh méo chữ)
    svg_path = (
        f'<path d="M 0,100 L {points_str_t} L {x_last_pct},100 Z" '
        'fill="rgba(255,61,0,0.08)"/>'
    )
    svg_line = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="1.8" '
        f'stroke-linecap="round" vector-effect="non-scaling-stroke" points="{points_str_t}" />'
    )

    # Khung bao lưới (Borders) bằng nét mảnh không dãn
    axis_color = "#455a64"
    svg_axes = (
        f'<line x1="0" y1="0" x2="0" y2="100" stroke="{axis_color}" stroke-width="1.2" vector-effect="non-scaling-stroke" />'
        f'<line x1="100" y1="0" x2="100" y2="100" stroke="{axis_color}" stroke-width="1.2" vector-effect="non-scaling-stroke" />'
        f'<line x1="0" y1="100" x2="100" y2="100" stroke="{axis_color}" stroke-width="1.2" vector-effect="non-scaling-stroke" />'
    )

    svg_graphics = (
        '<svg viewBox="0 0 100 100" preserveAspectRatio="none" '
        'style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible;">'
        f"{svg_axes}{columns_svg}{svg_path}{proj_lines}{svg_line}{data_nodes}</svg>"
    )

    # ============================================================
    # LỚP MẶT HTML (AXIS LABELS) - ĐỊNH VỊ BẰNG % CSS (KHÔNG LO MÉO CHỮ)
    # ============================================================
    # Nhãn Trục Y Trái (Nhiệt độ - Màu đỏ cam)
    MIN_LABEL_GAP = 12.0  # Khoảng cách tối thiểu bằng % chiều cao để tránh đè chữ
    y_labels_temp_list = [
        f'<span class="axis-label y-label-left" style="top: {y_start_pct}%;">{t_start:.1f}°C</span>'
    ]
    if abs(y_peak_pct - y_start_pct) >= MIN_LABEL_GAP:
        y_labels_temp_list.append(
            f'<span class="axis-label y-label-left" style="top: {y_peak_pct}%;">{t_peak:.1f}°C</span>'
        )
    if (
        abs(y_end_pct - y_start_pct) >= MIN_LABEL_GAP
        and abs(y_end_pct - y_peak_pct) >= MIN_LABEL_GAP
    ):
        y_labels_temp_list.append(
            f'<span class="axis-label y-label-left" style="top: {y_end_pct}%;">{t_end:.1f}°C</span>'
        )
    y_labels_temp = "".join(y_labels_temp_list)

    # Nhãn Trục Y Phải (Khói - Màu xanh lam)
    y_labels_smoke = (
        f'<span class="axis-label y-label-right" style="top: 0%;">{smoke_ceiling:.0f} PPM</span>'
        f'<span class="axis-label y-label-right" style="top: 50%;">{smoke_ceiling / 2:.0f} PPM</span>'
        f'<span class="axis-label y-label-right" style="top: 100%;">0 PPM</span>'
    )

    # Nhãn Trục X (Thời gian)
    MIN_X_GAP = 15.0  # Khoảng cách tối thiểu % chiều rộng tránh đè chữ thời gian
    x_labels_list = [
        f'<span class="axis-label x-label" style="left: 0%; transform: none;">{time_start}</span>'
    ]
    if abs(x_peak_pct - 0.0) >= MIN_X_GAP and abs(100.0 - x_peak_pct) >= MIN_X_GAP:
        x_labels_list.append(
            f'<span class="axis-label x-label" style="left: {x_peak_pct}%; transform: translateX(-50%);">{time_peak}</span>'
        )
    x_labels_list.append(
        f'<span class="axis-label x-label" style="left: 100%; transform: translateX(-100%);">{time_end}</span>'
    )
    x_labels = "".join(x_labels_list)

    # Toàn bộ Style Scoped cho Pancake Layering
    styles = """
    <style>
        .pancake-container {
            position: relative;
            height: 160px;
            margin: 20px 80px 35px 70px;
        }
        .axis-label {
            position: absolute;
            font-size: 11px;
            font-family: sans-serif;
            white-space: nowrap;
            line-height: 1;
            pointer-events: none;
        }
        .y-label-left {
            right: 100%;
            margin-right: 10px;
            transform: translateY(-50%);
            color: #ff5722;
        }
        .y-label-right {
            left: 100%;
            margin-left: 10px;
            transform: translateY(-50%);
            color: #2196f3;
        }
        .x-label {
            top: 100%;
            margin-top: 10px;
        }
    </style>
    """

    html_graphics = (
        f'<div class="pancake-container">'
        f"{styles}"
        f"{svg_graphics}"
        f"{y_labels_temp}"
        f"{y_labels_smoke}"
        f"{x_labels}"
        f"</div>"
    )

    # 8. THANH ĐIỀU HƯỚNG ZOOM ĐỘNG
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
            f"{zoom_bar}{html_graphics}</div>"
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
            f"{status_bar}{zoom_bar}{html_graphics}</div>"
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
            f"{status_bar}{zoom_bar}{html_graphics}</div>"
        )

    return Response(content=wrapper, media_type="text/html")
