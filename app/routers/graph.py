from typing import cast

from fastapi import APIRouter, Response

import app.database as database


__all__ = ["router"]

router = APIRouter()




def _build_svg(
    points_t: list[float],
    points_s: list[float],
    times: list[str],
    zoom: str,
) -> str:
    """Vẽ SVG chart kép: đường khói + đường nhiệt độ cùng normalized 0-100%."""
    w, h = 800, 200
    p_left, p_right, p_top, p_bottom = 60, 60, 20, 30
    chart_w = w - p_left - p_right
    chart_h = h - p_top - p_bottom
    y_base = h - p_bottom
    num_pts = len(points_t)

    # 1. Chuẩn hóa nhiệt độ qua hàm dynamic span
    min_span_t = 2.0 if zoom == "detail" else 40.0
    min_t, max_t = min(points_t), max(points_t)
    span_t = max_t - min_t
    if span_t < min_span_t:
        diff = min_span_t - span_t
        min_t = max(20.0, min_t - diff / 2.0)
        max_t = min_t + min_span_t
        span_t = min_span_t

    norm_t = [(v - min_t) / span_t * 100.0 for v in points_t]

    # 2. Chuẩn hóa khói (0-100% theo min/max thực tế, bảo vệ bằng min_span_s)
    min_span_s = 50.0 if zoom == "detail" else 300.0
    min_s = min(points_s) if points_s else 0.0
    max_s = max(points_s) if points_s else 300.0
    span_s = max_s - min_s
    if span_s < min_span_s:
        diff = min_span_s - span_s
        min_s = max(0.0, min_s - diff / 2.0)
        max_s = min_s + min_span_s
        span_s = min_span_s

    norm_s = [(s - min_s) / span_s * 100.0 for s in points_s]

    def x_pos(i: int) -> float:
        return p_left + (i / (num_pts - 1)) * chart_w if num_pts > 1 else p_left

    def y_pos(pct: float) -> float:
        return y_base - (pct / 100.0) * chart_h

    axis_color = "#455a64"
    text_color = "#9e9e9e"
    grid_color = "rgba(255,255,255,0.22)"

    # Trục chính
    axes = (
        f'<line x1="{p_left}" y1="{p_top}" x2="{p_left}" y2="{y_base}" stroke="{axis_color}" stroke-width="1.2"/>'
        f'<line x1="{w - p_right}" y1="{p_top}" x2="{w - p_right}" y2="{y_base}" stroke="{axis_color}" stroke-width="1.2"/>'
        f'<line x1="{p_left}" y1="{y_base}" x2="{w - p_right}" y2="{y_base}" stroke="{axis_color}" stroke-width="1.2"/>'
    )

    # Gridlines ngang: 0%, 50%, 100% kèm nhãn thật 2 bên trục Y
    grids = ""
    mid_t = (max_t + min_t) / 2.0
    mid_s = (max_s + min_s) / 2.0
    y_ticks_left = {100: f"{max_t:.1f}°C", 50: f"{mid_t:.1f}°C", 0: f"{min_t:.1f}°C"}
    y_ticks_right = {
        100: f"{max_s:.0f} ADC",
        50: f"{mid_s:.0f} ADC",
        0: f"{min_s:.0f} ADC",
    }

    for pct in [100, 50, 0]:
        yg = y_pos(float(pct))
        grids += (
            f'<line x1="{p_left}" y1="{yg}" x2="{w - p_right}" y2="{yg}" stroke="{grid_color}" stroke-dasharray="3"/>'
            f'<text x="{p_left - 8}" y="{yg + 4:.1f}" text-anchor="end" fill="#ff5722" font-size="10">{y_ticks_left[pct]}</text>'
            f'<text x="{w - p_right + 8}" y="{yg + 4:.1f}" text-anchor="start" fill="#2196f3" font-size="10">{y_ticks_right[pct]}</text>'
        )

    # Đường khói (Smoke line & area)
    coords_s = [(x_pos(i), y_pos(p)) for i, p in enumerate(norm_s)]
    pts_s_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords_s)
    x0, xn = coords_s[0][0], coords_s[-1][0]
    
    fill_smoke = (
        f'<path d="M {x0:.1f},{y_base} L {pts_s_str} L {xn:.1f},{y_base} Z" '
        f'fill="rgba(33,150,243,0.05)"/>'
    )
    line_smoke = (
        f'<polyline fill="none" stroke="#2196f3" stroke-width="1.6" '
        f'stroke-linecap="round" points="{pts_s_str}"/>'
    )

    # Đường nhiệt độ (Temperature line & area)
    coords_t = [(x_pos(i), y_pos(p)) for i, p in enumerate(norm_t)]
    pts_t_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords_t)
    
    fill_temp = (
        f'<path d="M {x0:.1f},{y_base} L {pts_t_str} L {xn:.1f},{y_base} Z" '
        f'fill="rgba(255,61,0,0.06)"/>'
    )
    line_temp = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="1.8" '
        f'stroke-linecap="round" points="{pts_t_str}"/>'
    )

    # Điểm peak + projection lines
    peak_t_idx = norm_t.index(max(norm_t))
    xp_t, yp_t = coords_t[peak_t_idx]
    
    peak_s_idx = norm_s.index(max(norm_s))
    xp_s, yp_s = coords_s[peak_s_idx]

    proj = (
        f'<line x1="{p_left}" y1="{yp_t:.1f}" x2="{w - p_right}" y2="{yp_t:.1f}" stroke="{grid_color}" stroke-dasharray="4"/>'
        f'<line x1="{xp_t:.1f}" y1="{y_base}" x2="{xp_t:.1f}" y2="{p_top}" stroke="{grid_color}" stroke-dasharray="4"/>'
        f'<line x1="{xp_s:.1f}" y1="{y_base}" x2="{xp_s:.1f}" y2="{p_top}" stroke="{grid_color}" stroke-dasharray="4"/>'
    )

    # Nodes cho cả 2
    nodes = (
        f'<circle cx="{coords_t[0][0]:.1f}" cy="{coords_t[0][1]:.1f}" r="3" fill="#ff3d00"/>'
        f'<circle cx="{xp_t:.1f}" cy="{yp_t:.1f}" r="4" fill="#ff3d00"/>'
        f'<circle cx="{coords_t[-1][0]:.1f}" cy="{coords_t[-1][1]:.1f}" r="3" fill="#ff3d00"/>'
        f'<circle cx="{coords_s[0][0]:.1f}" cy="{coords_s[0][1]:.1f}" r="2" fill="#2196f3"/>'
        f'<circle cx="{xp_s:.1f}" cy="{yp_s:.1f}" r="3.5" fill="#2196f3"/>'
        f'<circle cx="{coords_s[-1][0]:.1f}" cy="{coords_s[-1][1]:.1f}" r="2" fill="#2196f3"/>'
    )

    # X labels: start, peak, end
    MIN_X_GAP = 60.0
    x_labels = f'<text x="{x0:.1f}" y="{h - 8}" text-anchor="start" fill="{text_color}" font-size="10">{times[0]}</text>'
    
    xp_lbl = xp_t
    lbl_idx = peak_t_idx
    if abs(xp_s - x0) >= MIN_X_GAP and abs(xn - xp_s) >= MIN_X_GAP:
        xp_lbl = xp_s
        lbl_idx = peak_s_idx
        
    if abs(xp_lbl - x0) >= MIN_X_GAP and abs(xn - xp_lbl) >= MIN_X_GAP:
        x_labels += f'<text x="{xp_lbl:.1f}" y="{h - 8}" text-anchor="middle" fill="{text_color}" font-size="10">{times[lbl_idx]}</text>'
    
    x_labels += f'<text x="{xn:.1f}" y="{h - 8}" text-anchor="end" fill="{text_color}" font-size="10">{times[-1]}</text>'

    return (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:{h}px;overflow:visible;display:block;">'
        f"{axes}{grids}{fill_smoke}{fill_temp}{proj}{line_smoke}{line_temp}{nodes}{x_labels}"
        f"</svg>"
    )


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str, zoom: str = "flat"):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    target_id: int = 0
    is_latest = incident_id == "latest"
    current_status = "safe"
    is_active: bool = False

    if not is_latest:
        # VÁ LỖI CHÍ MẠNG: Ép kiểu và bảo vệ an toàn cho incident_id [1]
        try:
            target_id = int(incident_id)
        except ValueError:
            return Response(
                content="<div class='padding center-align'>ID sự cố không hợp lệ.</div>",
                media_type="text/html",
                status_code=400,
            )

    # 1. Trạng thái hệ thống (chỉ khi latest)
    if is_latest:
        state_res = await database.db.execute(
            "SELECT status FROM system_state WHERE id = 1"
        )
        if state_res.rows:
            current_status = str(state_res.rows[0][0])

    # 2. Query dữ liệu sử dụng ORDER BY id ASC tránh lỗi lệch ngày [1]
    if is_latest:
        if current_status == "critical":
            id_res = await database.db.execute(
                "SELECT incident_id FROM incidents WHERE end_time = 'Active' "
                "ORDER BY incident_id DESC LIMIT 1"
            )
            if id_res.rows:
                target_id = int(cast(int, id_res.rows[0][0]))
                res = await database.db.execute(
                    "SELECT temp, smoke, timestamp FROM burning_logs "
                    "WHERE incident_id = ? ORDER BY id ASC",
                    [target_id],
                )
            else:
                res = await database.db.execute(
                    "SELECT temp, smoke, timestamp FROM ("
                    "  SELECT id, temp, smoke, timestamp FROM burning_logs "
                    "  ORDER BY id DESC LIMIT 30"
                    ") AS recent ORDER BY id ASC"
                )
        else:
            res = await database.db.execute(
                "SELECT temp, smoke, timestamp FROM ("
                "  SELECT id, temp, smoke, timestamp FROM burning_logs "
                "  WHERE incident_id = 0 ORDER BY id DESC LIMIT 30"
                ") AS recent ORDER BY id ASC"
            )
    else:
        res = await database.db.execute(
            "SELECT temp, smoke, timestamp FROM burning_logs "
            "WHERE incident_id = ? ORDER BY id ASC",
            [target_id],
        )
        id_res = await database.db.execute(
            "SELECT end_time FROM incidents WHERE incident_id = ?", [target_id]
        )
        if id_res.rows:
            is_active = str(id_res.rows[0][0]) == "Active"

    # 3. Parse rows
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

    # 4. Empty state
    if not points_t:
        back_btn = (
            ""
            if is_latest
            else (
                '<div class="center-align">'
                '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
                'hx-target=".graph-wrapper" hx-swap="outerHTML">'
                "<i>sensors</i> Trở về Ambient</button></div>"
            )
        )
        if is_latest:
            htmx = f'hx-get="/api/analytics/graph/latest?zoom={zoom}" hx-trigger="every 5s" hx-swap="outerHTML"'
        elif is_active:
            htmx = f'hx-get="/api/analytics/graph/{target_id}?zoom={zoom}" hx-trigger="every 5s" hx-swap="outerHTML"'
        else:
            htmx = 'hx-swap="outerHTML"'
        return Response(
            content=f'<div class="graph-wrapper margin" {htmx}><p class="padding center-align">Chưa có dữ liệu đo đạc.</p>{back_btn}</div>',
            media_type="text/html",
        )

    # 5. Render SVG
    svg = _build_svg(points_t, points_s, times, zoom)

    # 6. Zoom bar + legend
    cf = "primary" if zoom == "flat" else "outline"
    cd = "primary" if zoom == "detail" else "outline"
    zoom_bar = (
        f'<div class="row" style="gap:6px;justify-content:space-between;margin-bottom:8px;align-items:center;">'
        f'<div style="display:flex;gap:14px;font-size:12px;color:#9e9e9e;">'
        f'<span style="display:flex;align-items:center;gap:5px;">'
        f'<span style="width:18px;height:2px;background:#ff3d00;border-radius:1px;display:inline-block;"></span>Nhiệt độ (°C)</span>'
        f'<span style="display:flex;align-items:center;gap:5px;">'
        f'<span style="width:18px;height:2px;background:#2196f3;border-radius:1px;display:inline-block;"></span>Khói (ADC)</span>'
        f"</div>"
        f'<div style="display:flex;gap:6px;">'
        f'<button class="chip {cf}" hx-get="/api/analytics/graph/{incident_id}?zoom=flat" hx-target=".graph-wrapper" hx-swap="outerHTML">Mặc định</button>'
        f'<button class="chip {cd}" hx-get="/api/analytics/graph/{incident_id}?zoom=detail" hx-target=".graph-wrapper" hx-swap="outerHTML">Zoom</button>'
        f"</div></div>"
    )

    # 7. Wrapper HTMX
    if is_latest:
        wrapper = (
            f'<div class="graph-wrapper margin" '
            f'hx-get="/api/analytics/graph/latest?zoom={zoom}" '
            f'hx-trigger="every 5s" hx-swap="outerHTML">'
            f"{zoom_bar}{svg}</div>"
        )
    elif is_active:
        status_bar = (
            '<div class="row margin valign" style="gap:12px;margin-bottom:16px;">'
            f'<span class="chip error padding blinking">Sự cố #{target_id} ĐANG DIỄN RA (LIVE)</span>'
            '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
            'hx-target=".graph-wrapper" hx-swap="outerHTML">'
            "<i>sensors</i> Trở về Ambient</button></div>"
        )
        wrapper = (
            f'<div class="graph-wrapper margin" '
            f'hx-get="/api/analytics/graph/{target_id}?zoom={zoom}" '
            f'hx-trigger="every 5s" hx-swap="outerHTML">'
            f"{status_bar}{zoom_bar}{svg}</div>"
        )
    else:
        status_bar = (
            '<div class="row margin valign" style="gap:12px;margin-bottom:16px;">'
            f'<span class="chip error padding">Lịch sử sự cố #{target_id}</span>'
            '<button class="chip primary" hx-get="/api/analytics/graph/latest" '
            'hx-target=".graph-wrapper" hx-swap="outerHTML">'
            "<i>sensors</i> Trở về Ambient</button></div>"
        )
        wrapper = (
            '<div class="graph-wrapper margin" hx-swap="outerHTML">'
            f"{status_bar}{zoom_bar}{svg}</div>"
        )

    return Response(content=wrapper, media_type="text/html")
