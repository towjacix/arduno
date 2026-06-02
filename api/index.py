from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

# Import toàn bộ module database
import app.database as database
from app.routers import graph, monitor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Khởi tạo client tại đây (Giải quyết lỗi RuntimeError)
    # Linter sẽ thấy database.create_client là hợp lệ
    database.db = database.create_client(
        url=database.TURSO_URL, auth_token=database.TURSO_TOKEN
    )

    yield

    # Đóng kết nối khi shutdown
    if database.db is not None:
        await database.db.close()


app = FastAPI(lifespan=lifespan)

# Đăng ký các router
app.include_router(monitor.router)
app.include_router(graph.router)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    # Phục vụ giao diện
    base_path = Path(__file__).resolve().parent.parent
    html_path = base_path / "beer_css_framework" / "webpage.html"

    try:
        with open(html_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>404: HTML Not Found</h1>"
