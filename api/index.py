from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.database import db
from app.routers import graph, monitor


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await db.close()


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
        return "<h1>Error: HTML file not found</h1>"


@app.get("/health")
async def health():
    return {"status": "ok", "database": "connected"}
