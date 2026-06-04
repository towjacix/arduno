import re
from typing import cast

from fastapi import APIRouter, Response

import app.database as database


__all__ = ["router"]

router = APIRouter()


# ── SVG dimensions & padding ──────────────────────────────────────────────────
_W, _H = 900, 260
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 56, 24, 28, 38


def _bezier_path(coords: list[tuple[float, float]]) -> str:
    """Tạo SVG path dùng Cubic Bezier để đường nhiệt độ mượt mà."""
    if len(coords) < 2:
        x, y = coords[0]
        return f"M {x:.1f},{y:.1f}"

    d = [f"M {coords[0][0]:.1f},{coords[0][1]:.1f}"]
    for i in range(1, len(coords)):
        x0, y0 = coords[i - 1]
        x1, y1 = coords[i]
        cx = (x0 + x1) / 2.0
        # Cubic bezier: 2 control points at horizontal midpoint
        d.append(f"C {cx:.1f},{y0:.1f} {cx:.1f},{y1:.1f} {x1:.1f},{y1:.1f}")
    return " ".join(d)


def _build_svg(
    points_t: list[float],
    points_s: list[float],
    times: list[str],
    zoom: str,
) -> str:
    """Vẽ combo chart: khói dạng Bar (blue rects) + nhiệt độ dạng Bezier Line (orange-red).
    Cả 2 normalize 0-100% trên cùng 1 trục Y.
    """
    chart_w = _W - _PAD_L - _PAD_R
    chart_h = _H - _PAD_T - _PAD_B
    y_base = _H - _PAD_B
    n = len(points_t)

    # ── 1. Normalize nhiệt độ ─────────────────────────────────────────────
    min_span_t = 2.0 if zoom == "detail" else 10.0
    min_t, max_t = min(points_t), max(points_t)
    span_t = max_t - min_t
    if span_t < min_span_t:
        diff = min_span_t - span_t
        min_t = max(0.0, min_t - diff / 2.0)
        max_t = min_t + min_span_t
        span_t = min_span_t
    norm_t = [(v - min_t) / span_t * 100.0 for v in points_t]

    # ── 2. Normalize khói ─────────────────────────────────────────────────
    min_span_s = 50.0 if zoom == "detail" else 200.0
    min_s, max_s = 0.0, max(points_s) if points_s else 300.0
    span_s = max_s - min_s
    if span_s < min_span_s:
        max_s = min_s + min_span_s
        span_s = min_span_s
    norm_s = [min(100.0, (s - min_s) / span_s * 100.0) for s in points_s]

    # ── 3. Helpers vị trí ─────────────────────────────────────────────────
    def xc(i: int) -> float:
        """Tâm X của điểm i."""
        return _PAD_L + (i / (n - 1)) * chart_w if n > 1 else _PAD_L + chart_w / 2

    def yp(pct: float) -> float:
        """Y từ 0-100% (0% = đáy, 100% = đỉnh)."""
        return y_base - (pct / 100.0) * chart_h

    # ── 4. Màu sắc ────────────────────────────────────────────────────────
    COL_TEMP  = "#ff5722"
    COL_SMOKE = "#2196f3"
    COL_GRID  = "rgba(255,255,255,0.10)"
    COL_TEXT  = "#9e9e9e"
    COL_AX    = "#37474f"

    # ── 5. Grid lines ngang (0%, 50%, 100%) ──────────────────────────────
    y_ticks = {100: "100%", 50: "50%", 0: "0%"}
    grids = ""
    for pct, lbl in y_ticks.items():
        yg = yp(float(pct))
        grids += (
            f'<line x1="{_PAD_L}" y1="{yg:.1f}" x2="{_W - _PAD_R}" y2="{yg:.1f}" '
            f'stroke="{COL_GRID}" stroke-dasharray="6,4"/>'
            f'<text x="{_PAD_L - 6}" y="{yg + 4:.1f}" text-anchor="end" '
            f'fill="{COL_TEXT}" font-size="11" font-family="Outfit,sans-serif">{lbl}</text>'
        )

    # ── 6. Smoke bars (rect) ──────────────────────────────────────────────
    bar_gap = 0.18          # tỷ lệ khoảng cách giữa các bar
    slot_w  = chart_w / n if n > 1 else chart_w
    bar_w   = max(2.0, slot_w * (1 - bar_gap))
    bars = ""
    for i, pct in enumerate(norm_s):
        bh  = pct / 100.0 * chart_h
        bx  = _PAD_L + (i / n) * chart_w + (slot_w - bar_w) / 2
        by  = y_base - bh
        tip = f"{points_s[i]:.0f} ADC"
        bars += (
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'rx="2" fill="{COL_SMOKE}" fill-opacity="0.75">'
            f'<title>{tip}</title></rect>'
        )

    # ── 7. Temperature Bezier line + area fill ────────────────────────────
    coords_t = [(xc(i), yp(p)) for i, p in enumerate(norm_t)]
    path_d   = _bezier_path(coords_t)
    x0, xn_  = coords_t[0][0], coords_t[-1][0]

    # Vùng fill phía dưới đường nhiệt độ (rất nhẹ)
    fill_area = (
        f'<path d="{path_d} L {xn_:.1f},{y_base} L {x0:.1f},{y_base} Z" '
        f'fill="{COL_TEMP}" fill-opacity="0.08" stroke="none"/>'
    )
    # Đường chính
    line_temp = (
        f'<path d="{path_d}" fill="none" stroke="{COL_TEMP}" '
        f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
    )
    # Dots tại mỗi điểm đo
    dots = ""
    for i, (cx_, cy_) in enumerate(coords_t):
        r    = 4.5 if norm_t[i] == max(norm_t) else 3.0
        tip  = f"{points_t[i]:.1f}°C"
        dots += (
            f'<circle cx="{cx_:.1f}" cy="{cy_:.1f}" r="{r}" '
            f'fill="{COL_TEMP}" stroke="#1e1e20" stroke-width="1.2">'
            f'<title>{tip}</title></circle>'
        )

    # ── 8. Trục X ─────────────────────────────────────────────────────────
    # Hiển thị tối đa 10 nhãn, ưu tiên đầu / đuôi / đều đặn
    step   = max(1, n // 10)
    x_axis = (
        f'<line x1="{_PAD_L}" y1="{y_base}" x2="{_W - _PAD_R}" y2="{y_base}" '
        f'stroke="{COL_AX}" stroke-width="1"/>'
    )
    for i, t in enumerate(times):
        if i != 0 and i != n - 1 and i % step != 0:
            continue
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        x_axis += (
            f'<text x="{xc(i):.1f}" y="{_H - 8}" text-anchor="{anchor}" '
            f'fill="{COL_TEXT}" font-size="11" font-family="Outfit,sans-serif">{t}</text>'
        )

    # ── 9. Assemble SVG ───────────────────────────────────────────────────
    return (
        f'<svg viewBox="0 0 {_W} {_H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;overflow:visible;">'
        f"{grids}{bars}{fill_area}{line_temp}{dots}{x_axis}"
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
