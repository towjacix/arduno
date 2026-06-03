/*
 * arduno.ino — Lab Safety Monitor Firmware
 * Board  : Arduino Uno
 * WiFi   : ESP-01S (ESP8266) qua giao tiếp AT command / SoftwareSerial
 *
 * ┌─────────────────────────────────────────────────────┐
 * │  SƠ ĐỒ ĐẤU NỐI                                      │
 * │                                                     │
 * │  Arduino Uno          ESP-01S                       │
 * │  ─────────            ──────────                    │
 * │  D2 (RX) ◄──────────  TX                           │
 * │  D3 (TX) ──[1kΩ]──┬─  RX   ← chia áp 5V→3.3V      │
 * │                   └─[2kΩ]─ GND                     │
 * │  3.3V    ──────────  VCC  + CH_PD (EN)             │
 * │  GND     ──────────  GND                           │
 * │                                                     │
 * │  DHT22   DATA → D4  |  VCC → 5V  |  GND → GND     │
 * │  MQ-2    AOUT → A0  |  VCC → 5V  |  GND → GND     │
 * │                                                     │
 * │  ⚠  ESP-01S dùng 3.3V — KHÔNG cắm thẳng 5V!        │
 * │  ⚠  Dùng voltage divider (1kΩ + 2kΩ) ở chân RX     │
 * └─────────────────────────────────────────────────────┘
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

const unsigned long SEND_INTERVAL_MS = 2000;   // 2s — đồng bộ window_size
const unsigned int  ESP_BAUD         = 9600;    // ESP-01S mặc định; một số fw dùng 115200
const unsigned int  AT_TIMEOUT_MS    = 8000;    // timeout chờ phản hồi AT

// ─── BIẾN TOÀN CỤC ───────────────────────────────────────────────────────────
SoftwareSerial espSerial(ESP_RX_PIN, ESP_TX_PIN);
DHT dht(DHT_PIN, DHT_TYPE);
unsigned long lastSendTime = 0;
bool wifiReady = false;

// ─── HÀM TIỆN ÍCH AT ─────────────────────────────────────────────────────────

// Gửi lệnh AT và đợi chuỗi mong đợi trong timeout
bool atCommand(const String& cmd, const String& expect, unsigned int timeoutMs = AT_TIMEOUT_MS) {
  while (espSerial.available()) espSerial.read();   // xả buffer cũ

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

// Đọc toàn bộ phản hồi trong timeout (dùng sau CIPSEND)
String atRead(unsigned int timeoutMs = AT_TIMEOUT_MS) {
  String response = "";
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (espSerial.available()) {
      response += (char)espSerial.read();
    }
    if (response.indexOf("CLOSED") != -1 || response.indexOf("ERROR") != -1) {
      break;
    }
  }
  return response;
}

// ─── KẾT NỐI WIFI ────────────────────────────────────────────────────────────
bool connectWiFi() {
  Serial.println("[WiFi] Initializing ESP-01S...");

  // Reset module
  if (!atCommand("AT+RST", "ready", 5000)) {
    // Một số firmware trả về "OK" thay vì "ready"
    delay(2000);
  }
  delay(500);

  // Tắt echo (gọn log hơn)
  atCommand("ATE0", "OK", 2000);

  // Chế độ Station
  if (!atCommand("AT+CWMODE=1", "OK")) {
    Serial.println("[WiFi] Set mode failed");
    return false;
  }

  // Kết nối WiFi
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
void sendSensorData(float temp, int smoke) {
  // Xây dựng JSON payload
  // Dùng dtostrf thay vì sprintf để tránh lỗi float trên AVR
  char tempStr[8];
  dtostrf(temp, 4, 1, tempStr);
  String body = "{\"temp\":" + String(tempStr) + ",\"smoke\":" + String(smoke) + "}";
  int bodyLen = body.length();

  // Xây dựng HTTP request
  String httpRequest =
    "POST /api/monitor HTTP/1.1\r\n"
    "Host: " + String(API_HOST) + "\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: " + String(bodyLen) + "\r\n"
    "Connection: close\r\n"
    "\r\n" +
    body;

  int requestLen = httpRequest.length();

  // Mở kết nối SSL tới Vercel
  String cipStart = "AT+CIPSTART=\"SSL\",\"" + String(API_HOST) + "\",443";
  if (!atCommand(cipStart, "OK", 10000)) {
    Serial.println("[HTTP] CIPSTART failed — retrying WiFi");
    wifiReady = connectWiFi();
    return;
  }

  // Thông báo độ dài dữ liệu sắp gửi
  if (!atCommand("AT+CIPSEND=" + String(requestLen), ">", 5000)) {
    Serial.println("[HTTP] CIPSEND failed");
    atCommand("AT+CIPCLOSE", "OK", 2000);
    return;
  }

  // Gửi raw HTTP request
  espSerial.print(httpRequest);
  Serial.println("[HTTP] Sent payload: " + body);

  // Đọc phản hồi (tìm HTTP status)
  String resp = atRead(6000);
  if (resp.indexOf("200") != -1) {
    Serial.println("[API] OK 200");
  } else if (resp.indexOf("422") != -1) {
    Serial.println("[API] 422 Unprocessable — kiểm tra payload");
  } else {
    Serial.print("[API] Unexpected response: ");
    Serial.println(resp.substring(0, 80));
  }

  atCommand("AT+CIPCLOSE", "OK", 2000);
}

// ─── SETUP ───────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);        // Serial Monitor (Uno chỉ có 1 UART cứng)
  espSerial.begin(ESP_BAUD); // Giao tiếp với ESP-01S qua SoftwareSerial
  delay(100);

  dht.begin();

  Serial.println("=== Lab Safety Monitor ===");
  wifiReady = connectWiFi();
}

// ─── LOOP ────────────────────────────────────────────────────────────────────
void loop() {
  // Tự động kết nối lại nếu WiFi mất
  if (!wifiReady) {
    wifiReady = connectWiFi();
    delay(5000);
    return;
  }

  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    float temp = dht.readTemperature();
    float humidity = dht.readHumidity();

    if (isnan(temp) || isnan(humidity)) {
      Serial.println("[DHT22] Read error — bỏ qua lần này");
      return;
    }

    int smoke = analogRead(SMOKE_PIN);

    Serial.print("[Sensor] Temp: "); Serial.print(temp, 1);
    Serial.print("C | Humidity: "); Serial.print(humidity, 1);
    Serial.print("% | Smoke ADC: "); Serial.println(smoke);

    sendSensorData(temp, smoke);
  }
}
