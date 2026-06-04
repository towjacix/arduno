import re
from typing import cast

import pygal
from fastapi import APIRouter, Response
from pygal.style import Style

import app.database as database


__all__ = ["router"]

router = APIRouter()

# ── Pygal custom dark style matching the dashboard theme ────────────────────
_CHART_STYLE = Style(
    background="#1e1e20",
    plot_background="transparent",
    foreground="#9e9e9e",
    foreground_strong="#e0e0e0",
    foreground_subtle="#455a64",
    guide_stroke_color="rgba(255,255,255,0.14)",
    # Series colors: [0] = smoke bars (blue), [1] = temp line (orange-red)
    colors=("#2196f3", "#ff5722"),
    font_family="'Outfit', 'Segoe UI', sans-serif",
    label_font_size=11,
    legend_font_size=12,
    tooltip_font_size=11,
    title_font_size=0,
)


def _build_svg(
    points_t: list[float],
    points_s: list[float],
    times: list[str],
    zoom: str,
) -> str:
    """Vẽ pygal combo chart: khói dạng Bar (blue) + nhiệt độ dạng Line (orange-red).
    Cả 2 đã normalize 0-100% nên dùng 1 trục Y chung, nhãn thật hiển thị trên tooltip.
    """
    # ── 1. Normalize nhiệt độ ─────────────────────────────────────────────
    min_span_t = 2.0 if zoom == "detail" else 40.0
    min_t, max_t = min(points_t), max(points_t)
    span_t = max_t - min_t
    if span_t < min_span_t:
        diff = min_span_t - span_t
        min_t = max(20.0, min_t - diff / 2.0)
        max_t = min_t + min_span_t
        span_t = min_span_t
    norm_t = [(v - min_t) / span_t * 100.0 for v in points_t]

    # ── 2. Normalize khói ─────────────────────────────────────────────────
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

    # ── 3. Tính mid values cho Y-axis labels ──────────────────────────────
    mid_t = (max_t + min_t) / 2.0
    mid_s = (max_s + min_s) / 2.0

    # ── 4. Chọn X-axis labels (thưa dần cho nhiều điểm) ──────────────────
    n = len(times)
    if n <= 12:
        x_labels = times
    else:
        # Giữ nhãn đầu, cuối, và đều đặn tối đa 10 nhãn ở giữa
        step = max(1, n // 10)
        x_labels = [t if (i == 0 or i == n - 1 or i % step == 0) else "" for i, t in enumerate(times)]

    # ── 5. Build pygal Bar chart ──────────────────────────────────────────
    chart = pygal.Bar(
        style=_CHART_STYLE,
        width=900,
        height=260,
        show_legend=False,           # legend riêng trong zoom_bar HTML
        show_y_labels=True,
        show_x_labels=True,
        x_label_rotation=0,
        y_labels_major_every=2,
        show_minor_y_labels=False,
        show_dots=True,
        dots_size=3,
        stroke=True,
        stroke_style={"width": 2.2, "linecap": "round", "linejoin": "round"},
        fill=False,
        margin=4,
        margin_left=55,
        margin_right=55,
        margin_top=12,
        margin_bottom=20,
        legend_at_bottom=False,
        truncate_label=-1,
        show_x_guides=False,
        show_y_guides=True,
        inner_radius=0,
        spacing=2,
        y_title=None,
        x_title=None,
    )

    # Y labels: hiển thị giá trị thực bên trái (°C) và phải (ADC)
    chart.y_labels = [
        {"value": 0,   "label": f"{min_t:.0f}°C"},
        {"value": 50,  "label": f"{mid_t:.0f}°C"},
        {"value": 100, "label": f"{max_t:.0f}°C"},
    ]

    chart.x_labels = x_labels

    # Smoke bars — tooltip hiển thị giá trị ADC thật
    smoke_data = [
        {"value": pct, "label": f"{points_s[i]:.0f} ADC", "xlink": None}
        for i, pct in enumerate(norm_s)
    ]
    chart.add("Khói (ADC)", smoke_data)

    # Temperature line overlay — tooltip hiển thị °C thật
    temp_data = [
        {"value": pct, "label": f"{points_t[i]:.1f}°C", "xlink": None}
        for i, pct in enumerate(norm_t)
    ]
    chart.add("Nhiệt độ (°C)", temp_data, plotas="line")

    # ── 6. Render & patch SVG ─────────────────────────────────────────────
    svg_raw = chart.render().decode("utf-8")

    # Strip XML declaration và DOCTYPE (nếu có) để nhúng inline sạch
    svg_raw = re.sub(r"<\?xml[^?]*\?>", "", svg_raw).strip()

    # Patch: thêm style responsive vào thẻ <svg> gốc
    svg_patched = re.sub(
        r"<svg ",
        '<svg style="width:100%;height:auto;display:block;overflow:visible;" ',
        svg_raw,
        count=1,
    )

    return svg_patched


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
