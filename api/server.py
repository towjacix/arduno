import base64
import datetime
import json
import logging
import os

import requests
import toml
from fastapi import FastAPI, HTTPException, Request


logger = logging.getLogger(__name__)


app = FastAPI(title="IOT102 API Gateway")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPO")

# GitHub REST API endpoint configuration
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
URL_CONFIG = f"https://api.github.com/repos/{REPO}/contents/config/config.toml"
URL_STATES = f"https://api.github.com/repos/{REPO}/contents/states.json"
URL_TELEGRAM = f"https://api.telegram.org/bot{os.getenv('TELE_BOT_TOKEN')}/sendMessage"
CHAT_ID = os.getenv("TELE_CHAT_ID")


def get_github_file(url):
    """Fetches and decodes a file from GitHub repository."""
    res = requests.get(url, headers=HEADERS, timeout=10)
    if res.status_code == 200:
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]
    return None, None


@app.post("/api/v1/update-status")
async def handle_arduino_post(request: Request):
    try:
        payload = await request.json()
        temp = int(payload.get("temp", 0))
        smoke = int(payload.get("smoke", 0))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from e

    # Determine current system time and time-slot session
    now = datetime.datetime.now()
    current_time = now.strftime("%Y-%m-%d %H:%M:%S")
    current_hrs = now.hour

    if 0 <= current_hrs < 11:
        current_session = "sang"
    elif 11 <= current_hrs < 15:
        current_session = "trua"
    else:
        current_session = "chieu"

    # Fetch configuration rules from config.toml
    toml_raw, _ = get_github_file(URL_CONFIG)
    if not toml_raw:
        raise HTTPException(status_code=500, detail="Failed to load config.toml")
    config = toml.loads(toml_raw)

    mode = config.get("mode", "auto")
    smoke_threshold = config.get("threshold", {}).get("default", {}).get("smoke", 300)
    temp_offset = config.get("temp_offset", 10)

    # Fetch and parse dynamic state database from states.json
    json_raw, states_sha = get_github_file(URL_STATES)
    states_data = json.loads(json_raw) if json_raw else {}

    recent_logs = states_data.get("recent_logs", [])
    history_critical = states_data.get("history_critical", [])

    # Process adaptive temperature threshold logic
    if mode == "manual":
        dynamic_temp_threshold = (
            config.get("threshold", {}).get("default", {}).get("temp", 45)
        )
    else:
        # Check session change to handle overwrite mechanism
        if recent_logs:
            last_session = recent_logs[-1].get("session", "unknown")
            if current_session != last_session:
                recent_logs = []  # Overwrite and reset logs for the new session

        if recent_logs:
            avg_temp = sum(log["temp"] for log in recent_logs) / len(recent_logs)
        else:
            avg_temp = temp

        dynamic_temp_threshold = int(avg_temp + temp_offset)

    # Evaluate system alarm state
    status = "safe"
    if temp > dynamic_temp_threshold or smoke > smoke_threshold:
        status = "critical"

    # Append current reading to dynamic rolling logs
    new_log = {
        "temp": temp,
        "smoke": smoke,
        "timestamp": current_time,
        "session": current_session,
    }
    recent_logs.append(new_log)
    if len(recent_logs) > 12:
        recent_logs.pop(0)

    # Append incident snapshot to persistent history if state updates to critical
    old_status = states_data.get("current_status", {}).get("status", "safe")
    if status == "critical" and old_status != "critical":
        incident_log = {
            "timestamp": current_time,
            "status": status,
            "temp": temp,
            "smoke": smoke,
        }
        history_critical.append(incident_log)

        # Trigger active response broadcast via Telegram API
        if os.getenv("TELE_BOT_TOKEN") and CHAT_ID:
            msg = (
                f"[FIRE ALARM ACTIVE]\n"
                f"Status: CRITICAL\n"
                f"Temperature: {temp}C (Threshold: {dynamic_temp_threshold}C)\n"
                f"Smoke Level: {smoke} PPM\n"
                f"Timestamp: {current_time}"
            )
            try:
                requests.post(
                    URL_TELEGRAM, json={"chat_id": CHAT_ID, "text": msg}, timeout=5
                )
            except Exception as e:
                logger.error(f"Failed to send warning: {e}")

    # Construct complete JSON state store payload
    new_states_content = {
        "current_status": {
            "status": status,
            "timestamp": current_time,
            "temp": temp,
            "smoke": smoke,
            "current_dynamic_threshold": dynamic_temp_threshold,
        },
        "recent_logs": recent_logs,
        "history_critical": history_critical,
    }

    # Push updated state back to GitHub repository
    updated_json_str = json.dumps(new_states_content, indent=2)
    encoded_content = base64.b64encode(updated_json_str.encode("utf-8")).decode("utf-8")

    put_payload = {
        "message": f"System state auto-update [{current_time}]",
        "content": encoded_content,
    }
    if states_sha:
        put_payload["sha"] = states_sha

    put_res = requests.put(URL_STATES, headers=HEADERS, timeout=10, json=put_payload)
    if put_res.status_code not in [200, 201]:
        raise HTTPException(status_code=500, detail="Failed to sync state to GitHub")

    return {
        "status": "success",
        "system_time": current_time,
        "current_room_status": status,
    }
