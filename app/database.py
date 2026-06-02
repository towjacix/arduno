import os

from libsql_client import Client, create_client


# 1. LẤY THÔNG TIN XÁC THỰC TỪ BIẾN MÔI TRƯỜNG (ENVIRONMENT VARIABLES)
# Các giá trị này phải được set trên Vercel Dashboard (Settings > Environment Variables)
TURSO_URL: str = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN: str = os.getenv("TURSO_AUTH_TOKEN", "")

# Kiểm tra an toàn: Dừng hệ thống ngay nếu thiếu cấu hình Database
if not TURSO_URL or not TURSO_TOKEN:
    # Trong môi trường dev, có thể in ra cảnh báo. Trong production, raise lỗi.
    print("CRITICAL: Missing Turso Database credentials!")

# 2. KHỞI TẠO GLOBAL CLIENT
# Nhờ cơ chế module caching của Python, biến 'db' sẽ được khởi tạo một lần
# và giữ lại trên RAM của Vercel Instance cho các request sau (Warm Start).
db: Client = create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)


# 3. HELPER FUNCTIONS (Tùy chọn nhưng nên có)
async def check_connection():
    """Hàm test nhanh kết nối đến Turso"""
    try:
        await db.execute("SELECT 1")
        return True
    except Exception as e:
        print(f"Database connection error: {e}")
        return False
