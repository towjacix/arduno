from typing import cast

from fastapi import APIRouter, Response

from app.config import CONFIG
from app.database import db


router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str):
    """
    Vẽ đồ thị SVG cho một incident cụ thể hoặc 'latest'.
    Dữ liệu được lấy từ Turso và sắp xếp theo timestamp.
    """

    # 1. XỬ LÝ BIẾN incident_id (Hỗ trợ cả số và chữ 'latest')
    target_id: int = 0

    if incident_id == "latest":
        # Tìm ID lớn nhất trong bảng incidents
        id_query = "SELECT incident_id FROM incidents ORDER BY incident_id DESC LIMIT 1"
        id_res = await db.execute(id_query)
        if not id_res.rows:
            return Response(content="<p>No incidents found</p>", media_type="text/html")
        target_id = int(cast(int, id_res.rows[0][0]))
    else:
        try:
            target_id = int(incident_id)
        except ValueError:
            return Response(content="<p>Invalid ID format</p>", media_type="text/html")

    # 2. TRUY VẤN DỮ LIỆU ĐÃ SẮP XẾP
    # Sắp xếp theo timestamp ASC là cực kỳ quan trọng để đồ thị không bị rối
    query = "SELECT temp FROM burning_logs WHERE incident_id = ? ORDER BY timestamp ASC"
    res = await db.execute(query, [target_id])

    # Ép kiểu Value -> float và lọc None để Basedpyright không báo lỗi
    points: list[float] = []
    for r in res.rows:
        val = r[0]
        if isinstance(val, (int, float)):
            points.append(float(val))

    if not points:
        return Response(
            content=f"<p>Incident #{target_id}: No log data</p>", media_type="text/html"
        )

    # 3. THÔNG SỐ UI TỪ CONFIG
    # Cast CONFIG['ui'] về dict để truy cập an toàn
    ui_cfg = cast(dict, CONFIG["ui"])
    graph_cfg = ui_cfg.get("graph", {})
    w = int(graph_cfg.get("width", 800))
    h = int(graph_cfg.get("height", 200))
    color = str(graph_cfg.get("stroke_color", "#ff3d00"))

    # Tính toán Scale Y
    min_t, max_t = min(points), max(points)
    if max_t == min_t:
        max_t += 1.0

    # 4. TÍNH TOÁN TỌA ĐỘ
    coords = []
    num_points = len(points)
    for i, temp in enumerate(points):
        x = (i / (num_points - 1)) * w if num_points > 1 else 0
        y = h - ((temp - min_t) / (max_t - min_t) * h)
        coords.append(f"{x},{y}")

    points_str = " ".join(coords)

    # 5. RENDER SVG (Kèm hiệu ứng vùng phủ - Area Chart)
    # Tách chuỗi để tránh lỗi Ruff Line Too Long
    svg = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:{h}px; overflow:visible; display:block;">'
        f"<defs>"
        f'<linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity="0.3"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f"</linearGradient>"
        f"</defs>"
        f'<path d="M 0,{h} L {points_str} L {w},{h} Z" fill="url(#areaGrad)" />'
        f'<polyline fill="none" stroke="{color}" stroke-width="3" '
        f'stroke-linecap="round" stroke-linejoin="round" points="{points_str}" />'
        f"</svg>"
    )

    return Response(content=svg, media_type="text/html")
