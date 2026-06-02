from typing import cast

from fastapi import APIRouter, Response

import app.database as database


router = APIRouter()


@router.get("/api/analytics/graph/{incident_id}")
async def get_incident_graph(incident_id: str):
    if database.db is None:
        return Response(content="Database Error", status_code=500)

    target_id: int = 0
    if incident_id == "latest":
        # Tìm ID lớn nhất
        id_q = "SELECT incident_id FROM incidents ORDER BY incident_id DESC LIMIT 1"
        id_res = await database.db.execute(id_q)
        if not id_res.rows:
            return Response(content="<p>No data</p>", media_type="text/html")
        # Ép kiểu giá trị ID
        target_id = int(cast(int, id_res.rows[0][0]))
    else:
        target_id = int(incident_id)

    # Truy vấn log
    q = "SELECT temp FROM burning_logs WHERE incident_id = ? ORDER BY timestamp ASC"
    res = await database.db.execute(q, [target_id])

    # --- CÁCH FIX LỖI BASED PYRIGHT [Dòng 32] ---
    points: list[float] = []
    for row in res.rows:
        val = row[0]  # Lấy giá trị ra biến tạm
        # Kiểm tra kiểu dữ liệu tường minh để linter chắc chắn không phải None
        if isinstance(val, (int, float)):
            points.append(float(val))

    if not points:
        return Response(content="<p>Waiting for data...</p>", media_type="text/html")

    # Thông số vẽ
    w, h = 800, 200
    min_t, max_t = min(points), max(points)
    if max_t == min_t:
        max_t += 1.0

    # Tính toán tọa độ (Ngắt dòng để tránh lỗi Ruff E501)
    coords: list[str] = []
    num_pts = len(points)
    for i, v in enumerate(points):
        x = (i / (num_pts - 1)) * w if num_pts > 1 else 0
        y = h - ((v - min_t) / (max_t - min_t) * h)
        coords.append(f"{x},{y}")

    points_str = " ".join(coords)

    # Render SVG String
    svg_path = (
        f'<path d="M 0,{h} L {points_str} L {w},{h} Z" fill="rgba(255,61,0,0.1)"/>'
    )
    svg_line = (
        f'<polyline fill="none" stroke="#ff3d00" stroke-width="3" '
        f'stroke-linecap="round" points="{points_str}" />'
    )

    svg = (
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:{h}px; overflow:visible;">'
        f"{svg_path}{svg_line}"
        f"</svg>"
    )

    return Response(content=svg, media_type="text/html")
