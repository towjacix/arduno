import os

from libsql_client import Client, create_client


# URL phải được cấu hình là https://... trong Vercel Environment Variables
TURSO_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# Khởi tạo db là None
db: Client = None  # type: ignore


def init_db():
    global db
    if not TURSO_URL:
        import logging
        logging.warning("TURSO_DATABASE_URL is not set — database disabled")
        return
    # Nếu URL bắt đầu bằng https://, libsql-client sẽ tự động dùng HTTP API
    # Điều này tránh được lỗi WSS Handshake (WebSocket) trên Serverless
    db = create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)
