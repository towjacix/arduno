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
    """Combo chart: khói dạng Bar + nhiệt độ dạng Bezier Line, cả 2 normalized 0-100%.
    Tooltip CSS-only: hover vào cột sẽ hiện card với timestamp + giá trị gốc + %.
    """
    chart_w = _W - _PAD_L - _PAD_R
    chart_h = _H - _PAD_T - _PAD_B
    y_base  = _H - _PAD_B
    n       = len(points_t)

    # ── 1. Normalize nhiệt độ ─────────────────────────────────────────────
    min_span_t = 2.0 if zoom == "detail" else 10.0
    min_t, max_t = min(points_t), max(points_t)
    span_t = max_t - min_t
    if span_t < min_span_t:
        diff   = min_span_t - span_t
        min_t  = max(0.0, min_t - diff / 2.0)
        max_t  = min_t + min_span_t
        span_t = min_span_t
    norm_t = [(v - min_t) / span_t * 100.0 for v in points_t]

    # ── 2. Normalize khói ─────────────────────────────────────────────────
    min_span_s = 50.0 if zoom == "detail" else 200.0
    min_s, max_s = 0.0, max(points_s) if points_s else 300.0
    span_s = max_s - min_s
    if span_s < min_span_s:
        max_s  = min_s + min_span_s
        span_s = min_span_s
    norm_s = [min(100.0, (s - min_s) / span_s * 100.0) for s in points_s]

    # ── 3. Helpers ────────────────────────────────────────────────────────
    def xc(i: int) -> float:
        return _PAD_L + (i / (n - 1)) * chart_w if n > 1 else _PAD_L + chart_w / 2

    def yp(pct: float) -> float:
        return y_base - (pct / 100.0) * chart_h

    # ── 4. Màu ───────────────────────────────────────────────────────────
    COL_TEMP  = "#ff5722"
    COL_SMOKE = "#2196f3"
    COL_GRID  = "rgba(255,255,255,0.10)"
    COL_TEXT  = "#9e9e9e"
    COL_AX    = "#37474f"

    # ── 5. Embedded CSS (HTMX-safe vì nằm trong SVG) ──────────────────────
    # Tooltip card ẩn mặc định, hiện khi hover vào .tip-col
    TT_W, TT_H = 200, 78   # kích thước tooltip card
    css = (
        "<style>"
        ".tip-col .tt{display:none;pointer-events:none;}"
        ".tip-col:hover .tt{display:block;}"
        ".tip-col:hover .hit-bg{fill:rgba(255,255,255,0.04);}"
        "</style>"
    )

    # ── 6. Grid lines ─────────────────────────────────────────────────────
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

    # ── 7. Bezier path nhiệt độ (vẽ dưới các cột hover) ──────────────────
    coords_t = [(xc(i), yp(p)) for i, p in enumerate(norm_t)]
    path_d   = _bezier_path(coords_t)
    x0, xn_  = coords_t[0][0], coords_t[-1][0]

    fill_area = (
        f'<path d="{path_d} L {xn_:.1f},{y_base} L {x0:.1f},{y_base} Z" '
        f'fill="{COL_TEMP}" fill-opacity="0.07" stroke="none"/>'
    )
    line_temp = (
        f'<path d="{path_d}" fill="none" stroke="{COL_TEMP}" '
        f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # ── 8. Column groups: hit zone + bar + dot + tooltip card ─────────────
    slot_w = chart_w / n if n > 1 else chart_w
    bar_w  = max(2.0, slot_w * 0.82)
    cols   = ""

    for i in range(n):
        cx_   = xc(i)
        cy_   = coords_t[i][1]
        pct_s = norm_s[i]
        pct_t = norm_t[i]
        bh    = pct_s / 100.0 * chart_h
        bx    = _PAD_L + (i / n) * chart_w + (slot_w - bar_w) / 2
        by    = y_base - bh
        r_dot = 4.5 if pct_t == max(norm_t) else 3.0

        # ── Tooltip card position (clamp để không tràn ra ngoài) ──────────
        tt_x = max(_PAD_L, min(cx_ - TT_W / 2, _W - _PAD_R - TT_W))
        tt_y = _PAD_T + 2   # luôn gắn lên đầu chart

        # ── Nội dung tooltip ──────────────────────────────────────────────
        tt_time  = times[i]
        tt_temp  = f"{points_t[i]:.1f}\u00b0C ({pct_t:.0f}%)"
        tt_smoke = f"{points_s[i]:.0f} ADC ({pct_s:.0f}%)"

        tooltip = (
            f'<g class="tt" transform="translate({tt_x:.1f},{tt_y:.1f})">'
            # Shadow
            f'<rect x="2" y="2" width="{TT_W}" height="{TT_H}" rx="8" '
            f'fill="rgba(0,0,0,0.45)"/>'
            # Card background
            f'<rect width="{TT_W}" height="{TT_H}" rx="8" '
            f'fill="#16181d" stroke="rgba(255,255,255,0.13)" stroke-width="1"/>'
            # Timestamp header
            f'<text x="12" y="22" fill="#ffffff" font-size="13" '
            f'font-weight="600" font-family="Outfit,sans-serif">{tt_time}</text>'
            # Divider
            f'<line x1="12" y1="30" x2="{TT_W - 12}" y2="30" '
            f'stroke="rgba(255,255,255,0.10)" stroke-width="1"/>'
            # Temp row: icon + label
            f'<rect x="12" y="38" width="10" height="10" rx="2" fill="{COL_TEMP}"/>'
            f'<text x="28" y="48" fill="#e0e0e0" font-size="11.5" '
            f'font-family="Outfit,sans-serif">Nhi\u1ec7t \u0111\u1ed9: {tt_temp}</text>'
            # Smoke row: icon + label
            f'<rect x="12" y="56" width="10" height="10" rx="2" fill="{COL_SMOKE}"/>'
            f'<text x="28" y="66" fill="#e0e0e0" font-size="11.5" '
            f'font-family="Outfit,sans-serif">Kh\u00f3i: {tt_smoke}</text>'
            f'</g>'
        )

        cols += (
            f'<g class="tip-col">'
            # Hit zone: full-height transparent rect kích hoạt hover
            f'<rect class="hit-bg" x="{bx:.1f}" y="{_PAD_T}" '
            f'width="{bar_w:.1f}" height="{chart_h + 4}" rx="3" fill="transparent"/>'
            # Smoke bar
            f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'rx="2" fill="{COL_SMOKE}" fill-opacity="0.75"/>'
            # Temperature dot
            f'<circle cx="{cx_:.1f}" cy="{cy_:.1f}" r="{r_dot}" '
            f'fill="{COL_TEMP}" stroke="#1e1e20" stroke-width="1.5"/>'
            # Tooltip card
            f'{tooltip}'
            f'</g>'
        )

    # ── 9. Trục X ─────────────────────────────────────────────────────────
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

    # ── 10. Assemble SVG ──────────────────────────────────────────────────
    return (
        f'<svg viewBox="0 0 {_W} {_H}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;display:block;overflow:visible;">'
        f"{css}"
        f"{grids}"
        f"{fill_area}{line_temp}"
        f"{cols}"
        f"{x_axis}"
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
