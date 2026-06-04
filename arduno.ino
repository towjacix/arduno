/*
 * arduno.ino — Lab Safety Monitor Firmware
 * Board  : Arduino Uno
 * WiFi   : ESP-01S (ESP8266) qua giao tiếp AT command / SoftwareSerial
 *
 * ┌─────────────────────────────────────────────────────────────┐
 * │  SƠ ĐỒ ĐẤU NỐI                                              │
 * │                                                             │
 * │  Arduino Uno          ESP-01S                               │
 * │  ─────────            ──────────                            │
 * │  D2 (RX) ◄──────────  TX                                   │
 * │  D3 (TX) ──[1kΩ]──┬─  RX   ← chia áp 5V→3.3V              │
 * │                   └─[2kΩ]─ GND                             │
 * │  3.3V    ──────────  VCC  + CH_PD (EN)                     │
 * │  GND     ──────────  GND                                   │
 * │                                                             │
 * │  DHT22   DATA → D4  |  VCC → 5V  |  GND → GND             │
 * │  MQ-2    AOUT → A0  |  VCC → 5V  |  GND → GND             │
 * │  BUZZER  (+)  → D5  |  (−) → GND                          │
 * │                                                             │
 * │  ⚠  ESP-01S dùng 3.3V — KHÔNG cắm thẳng 5V!                │
 * │  ⚠  Dùng voltage divider (1kΩ + 2kΩ) ở chân RX             │
 * └─────────────────────────────────────────────────────────────┘
 *
 * API Response (POST /api/monitor):
 *   { "status": "safe|critical", "level": "safe|warning|critical",
 *     "temp_pct": 45.2, "smoke_pct": 63.1 }
 *
 *   level = "safe"     → buzzer silent
 *   level = "warning"  → Morse: W (·──)  repeated
 *   level = "critical" → Morse: SOS (···───···) repeated
 *
 * Thư viện cần cài (Arduino Library Manager):
 *   - DHT sensor library  (Adafruit)
 *   - Adafruit Unified Sensor
 */

#include <SoftwareSerial.h>
#include <DHT.h>

// ─── CẤU HÌNH — THAY ĐỔI 3 DÒNG NÀY ─────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* API_HOST      = "your-project.vercel.app";   // không có https://
// ─────────────────────────────────────────────────────────────────────────────

#define ESP_RX_PIN  2    // Arduino D2 nhận TX từ ESP-01S
#define ESP_TX_PIN  3    // Arduino D3 gửi RX tới ESP-01S (qua voltage divider)
#define DHT_PIN     4
#define DHT_TYPE    DHT22
#define SMOKE_PIN   A0
#define BUZZER_PIN  5    // Active buzzer hoặc passive buzzer (dùng tone())

const unsigned long SEND_INTERVAL_MS = 2000;   // 2s — đồng bộ window_size
const unsigned int  ESP_BAUD         = 9600;
const unsigned int  AT_TIMEOUT_MS    = 8000;

// ─── MORSE CODE TIMING (ms) ──────────────────────────────────────────────────
// Theo chuẩn ITU-R M.1677: dot=1 unit, dash=3 units, gap=1 unit
// Ta dùng unit = 120ms (tốc độ nghe dễ trong phòng lab)
const int MORSE_DOT    = 120;   // ·  ngắn
const int MORSE_DASH   = 360;   // ─  dài
const int MORSE_SYM    = 120;   // khoảng cách giữa ký hiệu trong cùng chữ
const int MORSE_CHAR   = 360;   // khoảng cách giữa các chữ cái
const int MORSE_WORD   = 840;   // khoảng cách giữa từ / lần lặp

// ─── BIẾN TOÀN CỤC ───────────────────────────────────────────────────────────
SoftwareSerial espSerial(ESP_RX_PIN, ESP_TX_PIN);
DHT dht(DHT_PIN, DHT_TYPE);
unsigned long lastSendTime = 0;
bool wifiReady = false;
String lastLevel = "safe";   // theo dõi level hiện tại cho buzzer loop

// ─── BUZZER HELPERS ───────────────────────────────────────────────────────────

void buzz(int durationMs) {
  // Passive buzzer: dùng tone(). Active buzzer: dùng digitalWrite.
  // Uncomment dòng phù hợp với phần cứng bạn có:
  tone(BUZZER_PIN, 880, durationMs);   // passive buzzer ~880Hz
  // digitalWrite(BUZZER_PIN, HIGH);   // active buzzer
  delay(durationMs);
  // digitalWrite(BUZZER_PIN, LOW);    // active buzzer
  noTone(BUZZER_PIN);
}

void dot()  { buzz(MORSE_DOT);  delay(MORSE_SYM); }
void dash() { buzz(MORSE_DASH); delay(MORSE_SYM); }
void charGap() { delay(MORSE_CHAR - MORSE_SYM); }

// ─── MORSE PATTERNS ──────────────────────────────────────────────────────────
/*
 * WARNING  → Morse chữ "W" = ·──
 *   Pattern: DOT DASH DASH
 *   Âm thanh: nhẹ nhàng, cảnh báo — 1 lần rồi dừng
 *
 * CRITICAL → Morse "SOS" = ···───···
 *   S = ···  (3 dots)
 *   O = ───  (3 dashes)
 *   S = ···  (3 dots)
 *   Âm thanh: khẩn cấp — phát liên tục trong vòng lặp
 */

// Phát 1 lần "W" (warning)  ·──
void morseW() {
  dot(); dash(); dash();
}

// Phát 1 lần "SOS"  ···───···
void morseSOS() {
  // S ···
  dot(); dot(); dot(); charGap();
  // O ───
  dash(); dash(); dash(); charGap();
  // S ···
  dot(); dot(); dot();
}

// Phát pattern theo level — gọi không-blocking một lần mỗi vòng loop
void soundBuzzer(const String& level) {
  if (level == "warning") {
    morseW();
    delay(MORSE_WORD);
  } else if (level == "critical") {
    morseSOS();
    delay(MORSE_WORD);
  }
  // "safe" → không phát âm
}

// ─── HÀM TIỆN ÍCH AT ─────────────────────────────────────────────────────────

bool atCommand(const String& cmd, const String& expect, unsigned int timeoutMs = AT_TIMEOUT_MS) {
  while (espSerial.available()) espSerial.read();
  espSerial.println(cmd);
  Serial.println(">> " + cmd);
  String response = "";
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (espSerial.available()) {
      char c = (char)espSerial.read();
      response += c;
    }
    if (response.indexOf(expect) != -1) {
      Serial.print("<< "); Serial.println(response);
      return true;
    }
  }
  Serial.print("<< [TIMEOUT] "); Serial.println(response);
  return false;
}

String atRead(unsigned int timeoutMs = AT_TIMEOUT_MS) {
  String response = "";
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (espSerial.available()) {
      response += (char)espSerial.read();
    }
    if (response.indexOf("CLOSED") != -1 || response.indexOf("ERROR") != -1) break;
  }
  return response;
}

// ─── KẾT NỐI WIFI ────────────────────────────────────────────────────────────
bool connectWiFi() {
  Serial.println("[WiFi] Initializing ESP-01S...");
  if (!atCommand("AT+RST", "ready", 5000)) { delay(2000); }
  delay(500);
  atCommand("ATE0", "OK", 2000);
  if (!atCommand("AT+CWMODE=1", "OK")) {
    Serial.println("[WiFi] Set mode failed");
    return false;
  }
  Serial.println("[WiFi] Connecting to " + String(WIFI_SSID) + "...");
  String joinCmd = "AT+CWJAP=\"" + String(WIFI_SSID) + "\",\"" + String(WIFI_PASSWORD) + "\"";
  if (!atCommand(joinCmd, "WIFI GOT IP", 15000)) {
    Serial.println("[WiFi] Connection failed");
    return false;
  }
  Serial.println("[WiFi] Connected!");
  return true;
}

// ─── GỬI DỮ LIỆU LÊN API ────────────────────────────────────────────────────
// Trả về level nhận được từ server: "safe" | "warning" | "critical"
String sendSensorData(float temp, int smoke) {
  char tempStr[8];
  dtostrf(temp, 4, 1, tempStr);
  String body    = "{\"temp\":" + String(tempStr) + ",\"smoke\":" + String(smoke) + "}";
  int    bodyLen = body.length();

  String httpRequest =
    "POST /api/monitor HTTP/1.1\r\n"
    "Host: " + String(API_HOST) + "\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: " + String(bodyLen) + "\r\n"
    "Connection: close\r\n"
    "\r\n" +
    body;

  String cipStart = "AT+CIPSTART=\"SSL\",\"" + String(API_HOST) + "\",443";
  if (!atCommand(cipStart, "OK", 10000)) {
    Serial.println("[HTTP] CIPSTART failed — retrying WiFi");
    wifiReady = connectWiFi();
    return "safe";
  }

  if (!atCommand("AT+CIPSEND=" + String(httpRequest.length()), ">", 5000)) {
    Serial.println("[HTTP] CIPSEND failed");
    atCommand("AT+CIPCLOSE", "OK", 2000);
    return "safe";
  }

  espSerial.print(httpRequest);
  Serial.println("[HTTP] Sent: " + body);

  String resp = atRead(6000);
  atCommand("AT+CIPCLOSE", "OK", 2000);

  // ── Parse "level" từ JSON response ───────────────────────────────────────
  // Server trả về: {"status":"...","level":"safe|warning|critical",...}
  // Tìm chuỗi "level":"<value>" bằng indexOf — không cần JSON library
  String level = "safe";
  int levelIdx = resp.indexOf("\"level\":");
  if (levelIdx != -1) {
    int q1 = resp.indexOf('"', levelIdx + 8);   // sau dấu :
    int q2 = resp.indexOf('"', q1 + 1);
    if (q1 != -1 && q2 != -1 && q2 > q1) {
      level = resp.substring(q1 + 1, q2);
    }
  }

  Serial.print("[API] level="); Serial.println(level);

  // Log thêm thông tin %
  int tempPctIdx = resp.indexOf("\"temp_pct\":");
  if (tempPctIdx != -1) {
    // Trích temp_pct để hiển thị trên Serial Monitor
    int end = resp.indexOf(',', tempPctIdx);
    if (end == -1) end = resp.indexOf('}', tempPctIdx);
    String tpStr = resp.substring(tempPctIdx + 11, end);
    tpStr.trim();
    Serial.print("[API] temp_pct="); Serial.print(tpStr); Serial.println("%");
  }

  return level;
}

// ─── SETUP ───────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);
  espSerial.begin(ESP_BAUD);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);
  delay(100);

  dht.begin();

  Serial.println("=== Lab Safety Monitor ===");
  wifiReady = connectWiFi();
}

// ─── LOOP ────────────────────────────────────────────────────────────────────
void loop() {
  if (!wifiReady) {
    wifiReady = connectWiFi();
    delay(5000);
    return;
  }

  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    float temp     = dht.readTemperature();
    float humidity = dht.readHumidity();

    if (isnan(temp) || isnan(humidity)) {
      Serial.println("[DHT22] Read error — bỏ qua lần này");
      return;
    }

    int smoke = analogRead(SMOKE_PIN);

    Serial.print("[Sensor] Temp: "); Serial.print(temp, 1);
    Serial.print("C | Humidity: ");  Serial.print(humidity, 1);
    Serial.print("% | Smoke ADC: "); Serial.println(smoke);

    lastLevel = sendSensorData(temp, smoke);
  }

  // ── Buzzer driven by lastLevel, fires between sensor sends ────────────────
  // soundBuzzer() takes ~500–900ms per call (Morse pattern + word gap).
  // Between sends (2000ms) there is time for 1–2 SOS repetitions.
  soundBuzzer(lastLevel);
}
