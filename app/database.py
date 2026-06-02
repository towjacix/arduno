import os

from libsql_client import Client, create_client as _create_client


# Lấy thông số từ biến môi trường
TURSO_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# Xuất bản hàm này ra để index.py có thể thấy
create_client = _create_client

# Khởi tạo db là None. Sử dụng Optional để Type Checker không than phiền
db: Client = None  # type: ignore
