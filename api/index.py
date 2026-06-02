from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

import app.database as database
from app.routers import graph, monitor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Khởi tạo kết nối HTTP thay vì WSS
    database.init_db()
    yield
    # Đóng kết nối
    if database.db is not None:
        await database.db.close()


app = FastAPI(lifespan=lifespan)

app.include_router(monitor.router)
app.include_router(graph.router)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    base_path = Path(__file__).resolve().parent.parent
    html_path = base_path / "beer_css_framework" / "webpage.html"
    try:
        with open(html_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>404: HTML Not Found</h1>"
