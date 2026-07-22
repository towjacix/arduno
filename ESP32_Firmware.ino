/*
  LAB SAFETY MONITOR - ESP32 firmware
  ------------------------------------
  Wiring (matches the pinout table you shared):
    DHT22 sensor:
      VCC  -> 3.3V hoặc 5V
      GND  -> GND
      DATA -> GPIO 23
    MQ smoke/gas sensor:
      VCC -> 5V (VIN)
      GND -> GND
      AO  -> GPIO 34 (VP, analog-only input)
    Buzzer:
      VCC/+ -> GPIO 18
      -     -> GND

  Libraries needed (Arduino IDE -> Library Manager):
    - "DHT sensor library" by Adafruit (+ its dependency "Adafruit Unified Sensor")
    - "ArduinoJson" by Benoit Blanchon

  Board: any ESP32 dev board, "ESP32 Dev Module" in Arduino IDE.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>

// ------------------- USER CONFIG -------------------
const char* WIFI_SSID     = "chubengungo";
const char* WIFI_PASSWORD = "12345678";

// Replace with the IP/hostname of the machine running the .NET server,
// e.g. "http://192.168.1.50:5000/api/sensor"
const char* SERVER_URL = "http://10.33.44.27:5000/api/sensor";

// Alarm thresholds - keep these in sync with appsettings.json on the server,
// but the ESP32 makes its own decision locally so the buzzer still works
// even if WiFi/the server is down.
const float TEMP_THRESHOLD_C   = 50.0;   // °C
const int   SMOKE_THRESHOLD_ADC = 300;   // raw ADC reading from the MQ sensor

const unsigned long SEND_INTERVAL_MS = 2000; // how often to read + POST
// ----------------------------------------------------

#define DHTPIN   23
#define DHTTYPE  DHT22
#define MQ_PIN   34   // ADC1 channel, input-only pin (VP)
#define BUZZER_PIN 18

DHT dht(DHTPIN, DHTTYPE);

unsigned long lastSend = 0;
bool alarmActive = false;

void connectWiFi() {
  Serial.printf("Connecting to WiFi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
    // Don't block forever - if WiFi is slow/unavailable we still want the
    // sensor + buzzer safety loop to keep running.
    if (millis() - start > 15000) {
      Serial.println("\nWiFi connect timed out, will keep retrying in background.");
      return;
    }
  }
  Serial.println("\nWiFi connected, IP: " + WiFi.localIP().toString());
}

void setup() {
  Serial.begin(115200);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  dht.begin();
  connectWiFi();
}

// Reads sensors, updates the buzzer immediately (local, no network dependency),
// and returns true if values were read successfully.
bool readSensors(float &temperature, float &humidity, int &smoke, bool &alarm) {
  temperature = dht.readTemperature();
  humidity    = dht.readHumidity();
  smoke       = analogRead(MQ_PIN); // 0-4095 on ESP32's 12-bit ADC

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("Failed to read from DHT22 sensor!");
    return false;
  }

  alarm = (temperature >= TEMP_THRESHOLD_C) || (smoke >= SMOKE_THRESHOLD_ADC);

  // Drive the buzzer locally and instantly - this is the safety-critical
  // part and must not wait on WiFi or the HTTP request below.
  digitalWrite(BUZZER_PIN, alarm ? HIGH : LOW);
  alarmActive = alarm;

  return true;
}

void postReading(float temperature, float humidity, int smoke, bool alarm) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected, skipping upload (buzzer logic still runs locally).");
    connectWiFi(); // try to recover
    return;
  }

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(4000);

  StaticJsonDocument<256> doc;
  doc["temperature"] = temperature;
  doc["humidity"]    = humidity;
  doc["smoke"]       = smoke;
  doc["alarm"]       = alarm;

  String payload;
  serializeJson(doc, payload);

  int httpCode = http.POST(payload);
  if (httpCode > 0) {
    Serial.printf("POST -> %d : %s\n", httpCode, http.getString().c_str());
  } else {
    Serial.printf("POST failed: %s\n", http.errorToString(httpCode).c_str());
  }
  http.end();
}

void loop() {
  if (millis() - lastSend >= SEND_INTERVAL_MS) {
    lastSend = millis();

    float temperature, humidity;
    int smoke;
    bool alarm;

    if (readSensors(temperature, humidity, smoke, alarm)) {
      Serial.printf("Temp: %.1f C | Humidity: %.1f%% | Smoke: %d | Alarm: %s\n",
                    temperature, humidity, smoke, alarm ? "YES" : "no");
      postReading(temperature, humidity, smoke, alarm);
    }
  }

  // Keep the loop responsive; the buzzer state is already updated inside
  // readSensors() every cycle above.
}
