import os
import random
import time

import requests


# Đổi URL này thành URL Vercel của ông hoặc dùng biến môi trường MONITOR_URL
BASE_URL = os.getenv("MONITOR_URL", "http://localhost:8000")
MONITOR_URL = f"{BASE_URL.rstrip('/')}/api/monitor"

# Mã màu ANSI hiển thị terminal chuyên nghiệp
GREEN = "\x1b[32m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
RESET = "\x1b[0m"


def send_data(temp: float, smoke: int, phase_name: str):
    payload = {"temp": temp, "smoke": smoke}
    try:
        # Giới hạn timeout 5s đề phòng Serverless bị khởi động nguội (Cold Start)
        response = requests.post(MONITOR_URL, json=payload, timeout=5)
        status = response.json().get("status", "unknown").upper()

        # Đổi màu chữ trạng thái dựa trên kết quả trả về từ Vercel
        status_color = RED if status == "CRITICAL" else GREEN

        print(
            f"[{phase_name}] -> Sent: "
            f"T={YELLOW}{temp:4.1f}°C{RESET}, "
            f"S={YELLOW}{smoke:3d} PPM{RESET} | "
            f"Server Status: {status_color}{status}{RESET}"
        )
    except requests.exceptions.RequestException as e:
        # Nếu mất mạng tạm thời, script vẫn tiếp tục chạy chứ không bị sập
        print(f"[{phase_name}] -> {RED}Mạng lỗi tạm thời: {e}{RESET}")
    except Exception as e:
        print(f"[{phase_name}] -> {RED}Lỗi không xác định: {e}{RESET}")


def run_infinite_simulation():
    cycle_count = 1
    print(f"{CYAN}=== KHỞI CHẠY GIẢ LẬP SENSOR TỰ ĐỘNG VÔ HẠN ==={RESET}")
    print(f"Đường truyền: {MONITOR_URL}")
    print(f"Nhấn {YELLOW}Ctrl + C{RESET} để dừng chương trình.\n")

    while True:
        print(f"{CYAN}--- CHU KỲ GIẢ LẬP SỐ #{cycle_count} ---{RESET}")

        # GIAI ĐOẠN 1: Môi trường an toàn bình thường (12 mẫu - 24 giây)
        # Giúp thuật toán "học" nhiệt độ phòng ổn định để ép Threshold thích ứng lùi về mức an toàn
        print(
            f"{GREEN}[GIAI ĐOẠN 1] Đo đạc môi trường an toàn bình thường (Safe)...{RESET}"
        )
        for _ in range(12):
            t = round(random.uniform(27.5, 30.5), 1)
            s = random.randint(70, 110)
            send_data(t, s, "SAFE-ROOM")
            time.sleep(2)

        # GIAI ĐOẠN 2: Sự cố hỏa hoạn bùng phát (8 mẫu - 16 giây)
        # Tăng dần nhiệt độ và mật độ khói để mô phỏng sự cố thực tế và ghi nhận đỉnh nhiệt (Peak Temp)
        print(
            f"\n{RED}[GIAI ĐOẠN 2] CẢNH BÁO: Phát hiện sự cố nhiệt & khói tăng vọt!{RESET}"
        )
        for i in range(8):
            t = round(42.0 + (i * 2.5), 1)  # Nhiệt độ dốc dần từ 42°C lên 59.5°C
            s = 300 + (i * 60)  # Khói dốc dần từ 300 lên 720 PPM
            send_data(t, s, "FIRE-ALERT")
            time.sleep(2)

        # GIAI ĐOẠN 3: Dập lửa & Hạ nhiệt độ phòng (8 mẫu - 16 giây)
        # Nhiệt độ dốc xuống cực nhanh, khói tan dần, hệ thống phải tự bẻ luồng về Safe
        print(
            f"\n{YELLOW}[GIAI ĐOẠN 3] Đã dập lửa thành công, hệ thống đang hạ nhiệt...{RESET}"
        )
        for i in range(8):
            t = round(55.0 - (i * 3.5), 1)  # Nhiệt độ hạ nhanh về mốc ~27°C
            s = max(50, 600 - (i * 80))  # Khói tan nhanh về mốc an toàn
            send_data(t, s, "COOL-DOWN")
            time.sleep(2)

        print(
            f"\n{CYAN}Chu kỳ #{cycle_count} kết thúc. Chuẩn bị lặp lại sau 2 giây...\n{RESET}"
        )
        cycle_count += 1
        time.sleep(2)


if __name__ == "__main__":
    try:
        run_infinite_simulation()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Đã tắt trạm giả lập cảm biến an toàn.{RESET}")
