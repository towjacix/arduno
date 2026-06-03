"""
test_sensor.py — Integration tests for Lab Safety Monitor API.

Dùng FastAPI TestClient (in-process) thay vì HTTP tới localhost.
Không cần chạy server — không bị lỗi "Connection refused".

Chạy:  python -m pytest test_sensor.py -v
Hoặc:  python test_sensor.py
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Patch database TRƯỚC khi import app ──────────────────────────────────
# Tạo mock db trả về kết quả giống libsql-client thật
mock_db = AsyncMock()


def _make_result(rows):
    """Tạo mock Result object giống libsql-client."""
    res = MagicMock()
    res.rows = rows
    return res


# ── Quản lý state cho mock DB ────────────────────────────────────────────
_system_state = {
    "status": "safe",
    "temp": 28.0,
    "smoke": 80,
    "threshold": 45,
    "timestamp": "2025-01-01 00:00:00",
}

_incidents: list[dict] = []
_logs: list[dict] = []
_next_incident_id = 1


def _reset_state():
    """Reset tất cả state về ban đầu cho mỗi test."""
    global _next_incident_id
    _system_state.update(
        status="safe", temp=28.0, smoke=80, threshold=45,
        timestamp="2025-01-01 00:00:00",
    )
    _incidents.clear()
    _logs.clear()
    _next_incident_id = 1


async def _mock_execute(query: str, params=None):
    """Giả lập SQL queries dựa trên nội dung query string."""
    global _next_incident_id
    q = query.strip().upper()

    # ── SELECT ───────────────────────────────────────────────────────────
    if q.startswith("SELECT") and "SYSTEM_STATE" in q:
        if "STATUS" in q and "TEMP" not in q:
            return _make_result([(_system_state["status"],)])
        return _make_result([(
            _system_state["status"],
            _system_state["temp"],
            _system_state["smoke"],
            _system_state["threshold"],
            _system_state["timestamp"],
        )])

    if q.startswith("SELECT") and "AVG(TEMP)" in q:
        ambient = [l["temp"] for l in _logs if l["incident_id"] == 0]
        if not ambient:
            return _make_result([])
        limit = params[0] if params else 30
        window = ambient[-limit:]
        avg = sum(window) / len(window)
        return _make_result([(avg,)])

    if q.startswith("SELECT") and "INCIDENTS" in q:
        if "END_TIME = 'ACTIVE'" in q or "END_TIME = 'Active'" in q.replace("ACTIVE", "Active"):
            active = [i for i in _incidents if i["end_time"] == "Active"]
            if "INCIDENT_ID" in q and "PEAK_TEMP" in q:
                if active:
                    a = active[-1]
                    return _make_result([(a["id"], a["peak_temp"])])
                return _make_result([])
            if active:
                return _make_result([(active[-1]["id"],)])
            return _make_result([])

        if "END_TIME != 'ACTIVE'" in q or "END_TIME != 'Active'" in q.replace("ACTIVE", "Active"):
            closed = [i for i in _incidents if i["end_time"] != "Active"]
            if closed:
                last = closed[-1]
                return _make_result([(last["id"], last["end_time"])])
            return _make_result([])

        if "ORDER BY INCIDENT_ID DESC" in q:
            limit = params[0] if params else 20
            offset = params[1] if params and len(params) > 1 else 0
            sliced = list(reversed(_incidents))[offset:offset + limit]
            return _make_result([
                (i["id"], i["start_time"], i["end_time"], i["peak_temp"])
                for i in sliced
            ])

        if params and "INCIDENT_ID = ?" in q.replace("incident_id", "INCIDENT_ID"):
            tid = params[0]
            matched = [i for i in _incidents if i["id"] == tid]
            if matched:
                m = matched[0]
                if "END_TIME" in q:
                    return _make_result([(m["end_time"],)])
                return _make_result([(m["id"], m["start_time"], m["end_time"], m["peak_temp"])])
            return _make_result([])

        return _make_result([])

    if q.startswith("SELECT") and "BURNING_LOGS" in q:
        if params:
            tid = params[0]
            matched = [l for l in _logs if l["incident_id"] == tid]
        else:
            matched = _logs[-30:]
        return _make_result([
            (l["temp"], l["smoke"], l["timestamp"]) for l in matched
        ])

    # ── INSERT ───────────────────────────────────────────────────────────
    if q.startswith("INSERT") and "INCIDENTS" in q:
        _incidents.append({
            "id": _next_incident_id,
            "start_time": params[0],
            "end_time": "Active",
            "peak_temp": params[1],
        })
        _next_incident_id += 1
        return _make_result([])

    if q.startswith("INSERT") and "BURNING_LOGS" in q:
        _logs.append({
            "incident_id": params[0],
            "timestamp": params[1],
            "temp": params[2],
            "smoke": params[3],
        })
        return _make_result([])

    # ── UPDATE ───────────────────────────────────────────────────────────
    if q.startswith("UPDATE") and "SYSTEM_STATE" in q:
        _system_state["status"] = params[0]
        _system_state["timestamp"] = params[1]
        _system_state["temp"] = params[2]
        _system_state["smoke"] = params[3]
        _system_state["threshold"] = params[4]
        return _make_result([])

    if q.startswith("UPDATE") and "INCIDENTS" in q:
        if "PEAK_TEMP" in q:
            for i in _incidents:
                if i["id"] == params[1]:
                    i["peak_temp"] = params[0]
        elif "END_TIME" in q and "'ACTIVE'" in q.replace("'Active'", "'ACTIVE'"):
            if "WHERE INCIDENT_ID" in q:
                for i in _incidents:
                    if i["id"] == params[0]:
                        i["end_time"] = "Active"
            else:
                for i in _incidents:
                    if i["end_time"] == "Active":
                        i["end_time"] = params[0]
        return _make_result([])

    return _make_result([])


mock_db.execute = AsyncMock(side_effect=_mock_execute)
mock_db.close = AsyncMock()

# Patch database module trước khi FastAPI app được import
with patch.dict("os.environ", {"TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": ""}):
    import app.database as database
    database.db = mock_db

    from api.index import app

client = TestClient(app)


# ── ANSI colors cho output đẹp ───────────────────────────────────────────
G = "\x1b[32m"
R = "\x1b[31m"
C = "\x1b[36m"
Y = "\x1b[33m"
X = "\x1b[0m"


# ═══════════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestMonitorAPI:
    """Test /api/monitor endpoint transitions."""

    def setup_method(self):
        _reset_state()

    def test_safe_reading(self):
        """Gửi dữ liệu an toàn — hệ thống phải ở trạng thái 'safe'."""
        resp = client.post("/api/monitor", json={"temp": 28.5, "smoke": 90})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "safe"
        print(f"{G}✓ Safe reading accepted{X}")

    def test_critical_transition(self):
        """Nhiệt độ vượt ngưỡng → hệ thống chuyển sang 'critical'."""
        resp = client.post("/api/monitor", json={"temp": 60.0, "smoke": 400})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "critical"
        print(f"{R}✓ Critical transition triggered{X}")

    def test_incident_created_on_critical(self):
        """Khi chuyển sang critical, một incident mới phải được tạo."""
        client.post("/api/monitor", json={"temp": 60.0, "smoke": 400})
        assert len(_incidents) == 1
        assert _incidents[0]["end_time"] == "Active"
        assert _incidents[0]["peak_temp"] == 60.0
        print(f"{R}✓ Incident created with Active status{X}")

    def test_peak_temp_update(self):
        """Peak temp phải được cập nhật khi có giá trị cao hơn."""
        client.post("/api/monitor", json={"temp": 55.0, "smoke": 400})
        client.post("/api/monitor", json={"temp": 65.0, "smoke": 500})
        assert _incidents[0]["peak_temp"] == 65.0
        print(f"{Y}✓ Peak temp updated to 65.0°C{X}")

    def test_safe_recovery(self):
        """Hạ nhiệt → hệ thống phải chuyển về 'safe' (với hysteresis)."""
        # Trigger critical
        client.post("/api/monitor", json={"temp": 60.0, "smoke": 400})
        # Cool down dưới ngưỡng - hysteresis
        resp = client.post("/api/monitor", json={"temp": 25.0, "smoke": 50})
        assert resp.json()["status"] == "safe"
        assert _incidents[0]["end_time"] != "Active"
        print(f"{G}✓ System recovered to safe{X}")

    def test_hysteresis_prevents_flicker(self):
        """Nhiệt ở sát ngưỡng → hysteresis giữ critical, không flicker."""
        # Trigger critical
        client.post("/api/monitor", json={"temp": 60.0, "smoke": 400})
        # Gửi temp ngay dưới threshold nhưng trên (threshold - hysteresis)
        # threshold mặc định 45, hysteresis 2 → cần < 43 để về safe
        resp = client.post("/api/monitor", json={"temp": 44.0, "smoke": 50})
        assert resp.json()["status"] == "critical"
        print(f"{Y}✓ Hysteresis prevented state flicker{X}")

    def test_log_recorded(self):
        """Mỗi POST đều ghi log vào burning_logs."""
        client.post("/api/monitor", json={"temp": 30.0, "smoke": 100})
        assert len(_logs) == 1
        assert _logs[0]["temp"] == 30.0
        assert _logs[0]["incident_id"] == 0
        print(f"{G}✓ Log recorded with incident_id=0{X}")


class TestStatusAPI:
    """Test /api/status endpoint."""

    def setup_method(self):
        _reset_state()

    def test_status_returns_html(self):
        """Status endpoint trả về HTML card grid."""
        resp = client.get("/api/status")
        assert resp.status_code == 200
        assert "status-grid" in resp.text
        assert "°C" in resp.text
        print(f"{G}✓ Status returns valid HTML grid{X}")

    def test_status_shows_critical_class(self):
        """Khi system critical, HTML phải có class critical-bg."""
        _system_state["status"] = "critical"
        resp = client.get("/api/status")
        assert "critical-bg" in resp.text
        assert "white-text" in resp.text
        print(f"{R}✓ Critical status renders with alert styling{X}")


class TestHistoryAPI:
    """Test /api/history endpoint."""

    def setup_method(self):
        _reset_state()

    def test_empty_history(self):
        """Bảng lịch sử trống hiển thị thông báo rỗng."""
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert "Chưa có nhật ký" in resp.text
        print(f"{C}✓ Empty history shows placeholder{X}")

    def test_history_with_incidents(self):
        """Có incident → bảng lịch sử hiển thị đúng dữ liệu."""
        _incidents.append({
            "id": 1,
            "start_time": "2025-01-01 12:00:00",
            "end_time": "2025-01-01 12:05:00",
            "peak_temp": 55.0,
        })
        resp = client.get("/api/history")
        assert "#1" in resp.text
        assert "DONE" in resp.text
        assert "55.0" in resp.text
        print(f"{C}✓ History renders incident row{X}")

    def test_history_active_incident(self):
        """Active incident hiển thị nút LIVE."""
        _incidents.append({
            "id": 1,
            "start_time": "2025-01-01 12:00:00",
            "end_time": "Active",
            "peak_temp": 45.0,
        })
        resp = client.get("/api/history")
        assert "LIVE" in resp.text
        print(f"{R}✓ Active incident shows LIVE badge{X}")

    def test_pagination_offset(self):
        """Infinite scroll pagination trả về partial HTML."""
        for i in range(25):
            _incidents.append({
                "id": i + 1,
                "start_time": f"2025-01-01 {i:02d}:00:00",
                "end_time": f"2025-01-01 {i:02d}:05:00",
                "peak_temp": 50.0 + i,
            })
        resp = client.get("/api/history?offset=0&limit=20")
        assert "load-more" in resp.text
        print(f"{C}✓ Pagination returns load-more trigger{X}")


class TestGraphAPI:
    """Test /api/analytics/graph endpoints."""

    def setup_method(self):
        _reset_state()

    def test_graph_latest_empty(self):
        """Latest graph trống hiển thị thông báo."""
        resp = client.get("/api/analytics/graph/latest")
        assert resp.status_code == 200
        assert "Chưa có dữ liệu" in resp.text
        print(f"{C}✓ Empty graph shows no-data message{X}")

    def test_graph_latest_with_data(self):
        """Latest graph có dữ liệu render SVG."""
        for i in range(5):
            _logs.append({
                "incident_id": 0,
                "timestamp": f"2025-01-01 12:0{i}:00",
                "temp": 28.0 + i * 0.5,
                "smoke": 80 + i * 10,
            })
        resp = client.get("/api/analytics/graph/latest")
        assert resp.status_code == 200
        assert "<svg" in resp.text
        print(f"{G}✓ Graph renders SVG with data{X}")

    def test_graph_invalid_id(self):
        """Invalid incident ID trả về lỗi 400."""
        resp = client.get("/api/analytics/graph/abc")
        assert resp.status_code == 400
        print(f"{Y}✓ Invalid graph ID returns 400{X}")

    def test_graph_zoom_detail(self):
        """Zoom=detail thay đổi span tối thiểu."""
        for i in range(5):
            _logs.append({
                "incident_id": 0,
                "timestamp": f"2025-01-01 12:0{i}:00",
                "temp": 28.0,
                "smoke": 80,
            })
        resp = client.get("/api/analytics/graph/latest?zoom=detail")
        assert resp.status_code == 200
        assert "<svg" in resp.text
        print(f"{G}✓ Detail zoom renders correctly{X}")


class TestIndexPage:
    """Test / root endpoint."""

    def test_index_serves_html(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Lab Monitor" in resp.text or "LAB SAFETY MONITOR" in resp.text
        print(f"{G}✓ Index page serves dashboard HTML{X}")


class TestFullCycle:
    """End-to-end scenario: safe → fire → cooldown → safe."""

    def setup_method(self):
        _reset_state()

    def test_full_fire_lifecycle(self):
        """Mô phỏng đầy đủ: phòng bình thường → cháy → dập lửa → an toàn."""
        # Phase 1: Safe readings (seed ambient data)
        for _ in range(5):
            r = client.post("/api/monitor", json={"temp": 28.0, "smoke": 90})
            assert r.json()["status"] == "safe"

        # Phase 2: Fire breaks out
        r = client.post("/api/monitor", json={"temp": 60.0, "smoke": 500})
        assert r.json()["status"] == "critical"
        assert len(_incidents) == 1

        # Phase 3: Fire escalates — peak update
        r = client.post("/api/monitor", json={"temp": 72.0, "smoke": 700})
        assert r.json()["status"] == "critical"
        assert _incidents[0]["peak_temp"] == 72.0

        # Phase 4: Cooldown
        r = client.post("/api/monitor", json={"temp": 25.0, "smoke": 50})
        assert r.json()["status"] == "safe"
        assert _incidents[0]["end_time"] != "Active"

        # Verify logs count: 5 safe + 2 fire + 1 cooldown = 8
        assert len(_logs) == 8

        print(f"{G}✓ Full lifecycle: safe → critical → cooldown → safe{X}")
        print(f"  Incidents: {len(_incidents)}, Logs: {len(_logs)}")
        print(f"  Peak temp: {_incidents[0]['peak_temp']}°C")


# ═══════════════════════════════════════════════════════════════════════════
# Standalone runner (không cần pytest)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n{C}{'═' * 60}")
    print("   LAB SAFETY MONITOR — Integration Tests")
    print(f"{'═' * 60}{X}\n")

    test_classes = [
        TestMonitorAPI,
        TestStatusAPI,
        TestHistoryAPI,
        TestGraphAPI,
        TestIndexPage,
        TestFullCycle,
    ]

    total = 0
    passed = 0
    failed = 0

    for cls in test_classes:
        print(f"\n{Y}── {cls.__name__} ──{X}")
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]

        for method_name in methods:
            if hasattr(instance, "setup_method"):
                instance.setup_method()
            total += 1
            try:
                getattr(instance, method_name)()
                passed += 1
            except Exception as e:
                failed += 1
                print(f"{R}✗ {method_name}: {e}{X}")

    print(f"\n{C}{'─' * 60}{X}")
    color = G if failed == 0 else R
    print(f"{color}Results: {passed}/{total} passed, {failed} failed{X}\n")
