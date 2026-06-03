# Building Arduno: A Cloud-Native IoT Fire & Smoke Monitor

*From a bare Arduino Uno to a live dashboard on the edge — sensors, AT commands, serverless Python, and a database in the sky.*

---

## Background

Most fire monitoring systems in small labs and workshops fall into one of two categories: expensive commercial units that report to proprietary cloud services, or bare-bones smoke detectors with no data logging at all. Neither option gives you a live feed of temperature trends, a graph of how fast a fire grew, or a timestamped incident log you can review after the fact.

Arduno was built to fill that gap. The goal was a system that runs on cheap hardware, stores every sensor reading in a persistent cloud database, and serves a live dashboard accessible from any browser — without requiring a dedicated server, a Raspberry Pi, or any always-on infrastructure.

The name is a deliberate misspelling. The Arduino does less work than you might expect.

---

## System overview

At the highest level the system has three layers.

**The sensor layer** is an Arduino Uno connected to a DHT22 temperature sensor and an MQ-2 smoke sensor. Every two seconds it wakes up, takes a reading from both sensors, and sends the data to a cloud API. It has no display, no local storage, and no decision-making logic. Its only job is to measure and report.

**The API layer** is a FastAPI application deployed on Vercel as a serverless function. It receives sensor readings, runs the threshold algorithm, manages incident state, and serves the dashboard HTML. Because it runs on Vercel it costs nothing to host and scales automatically.

**The data layer** is a Turso database — a distributed SQLite service that exposes an HTTP API. This matters because Vercel serverless functions cannot hold open a persistent TCP connection between invocations. Turso's HTTP transport means every query is a stateless HTTPS request, which works perfectly in a serverless environment.

```
Arduino Uno
  + DHT22 (temp)
  + MQ-2  (smoke)
  + ESP-01S (WiFi)
        │
        │  POST /api/monitor  (JSON, every 2s)
        │  HTTPS via AT commands
        ▼
  ┌─────────────────────────────────────┐
  │  Vercel Serverless (FastAPI)        │
  │                                     │
  │  POST /api/monitor                  │
  │    → threshold check                │
  │    → incident state machine         │
  │    → write burning_logs             │
  │    → update system_state            │
  │                                     │
  │  GET /api/status                    │
  │    → live sensor cards (HTMX)       │
  │                                     │
  │  GET /api/history                   │
  │    → scrollable incident log (HTMX) │
  │                                     │
  │  GET /api/analytics/graph/:id       │
  │    → server-rendered SVG chart      │
  └──────────────┬──────────────────────┘
                 │
         Turso Edge DB (LibSQL/SQLite)
           system_state
           incidents
           burning_logs
```

The frontend is a single HTML file. There is no JavaScript framework, no build toolchain, and no bundler. It uses Beer CSS for the visual layer and HTMX for live updates — the browser polls the API every two seconds for sensor data and every ten seconds for the incident log, swapping in fresh HTML fragments without a full page reload.

---

## Hardware

### Why Arduino Uno instead of ESP32

An ESP32 or ESP8266 running MicroPython or Arduino firmware could handle WiFi natively and would be a simpler starting point. The choice of Arduino Uno with an external ESP-01S module was deliberate: it mirrors the kind of hardware constraint you find in real labs, where the microcontroller might already be committed to other I/O work and a separate WiFi module is bolted on.

It also makes the firmware more interesting. The Uno has a single hardware UART shared with the USB serial port, no native WiFi, and 2KB of RAM. Getting reliable HTTPS traffic out of it requires AT command choreography over a software serial port — a constraint that forces careful attention to timing and buffer management.

### Components

| Component | Function | Notes |
|-----------|----------|-------|
| Arduino Uno R3 | Main controller | ATmega328P, 16MHz, 2KB RAM |
| ESP-01S | WiFi module | ESP8266, communicates via AT commands |
| DHT22 | Temperature + humidity | ±0.5°C accuracy, digital one-wire |
| MQ-2 | Smoke / combustible gas | Outputs raw ADC 0–1023 |

### Wiring

The most important hardware constraint is voltage. The ESP-01S is a 3.3V device. The Arduino Uno runs at 5V. Connecting the Arduino's TX pin (5V logic) directly to the ESP-01S RX pin will damage the module over time and can cause unpredictable behaviour immediately.

The fix is a simple resistor voltage divider on the Arduino TX → ESP RX line: a 1kΩ resistor in series, followed by a 2kΩ resistor to ground. The junction between the two resistors gives approximately 3.3V when the Arduino outputs 5V HIGH. The return path (ESP TX → Arduino RX) is fine without level shifting, since the Uno's digital pins read 3.3V as a valid HIGH.

```
Arduino Uno          ESP-01S
───────────          ────────────────────────────
D2 (RX)  ◄─────────  TX
D3 (TX)  ──[1kΩ]──┬─ RX
                  └─[2kΩ]── GND

3.3V     ───────────  VCC
3.3V     ───────────  CH_PD (enable pin — must be HIGH)
GND      ───────────  GND

DHT22    DATA ─── D4 │ VCC ─── 5V │ GND ─── GND
MQ-2     AOUT ─── A0 │ VCC ─── 5V │ GND ─── GND
```

The CH_PD (chip power-down) pin must be tied HIGH for the ESP-01S to operate. Leaving it floating is a common mistake that results in the module appearing dead.

### A note on MQ-2 units

The MQ-2 datasheet describes output in PPM (parts per million) of various gases, but raw ADC readings from the analog output pin are not PPM. Converting ADC to PPM requires the sensor's characteristic curve, a known load resistance value, and a calibration step in clean air. Without calibration, the numbers are meaningless as PPM.

Arduno reports the raw ADC value (0–1023) and labels it accordingly. In clean indoor air the MQ-2 typically reads between 80 and 150 ADC. Near a lit match it spikes above 400. The smoke threshold in `config.toml` defaults to 300 ADC, which in practice gives a comfortable margin above normal variation without requiring hardware calibration.

---

## Firmware

### The AT command problem

The ESP-01S ships with Espressif's AT command firmware. From the Arduino's perspective the ESP-01S is a serial device that responds to text commands. You send `AT+CWJAP="ssid","password"\r\n` and wait for `WIFI GOT IP`. You send `AT+CIPSTART="SSL","host.com",443` and wait for `OK`. Then you send `AT+CIPSEND=N` where N is the byte count of your HTTP request, wait for the `>` prompt, blast the raw HTTP, and read the response.

This is workable but fragile. The AT firmware has its own flow control, its own response timing, and a tendency to emit unsolicited messages (`WIFI DISCONNECT`, `busy p...`) at inconvenient moments. The firmware uses a helper function that drains the serial buffer, sends a command, and waits up to a configurable timeout for an expected substring in the response.

```cpp
bool atCommand(const String& cmd, const String& expect, unsigned int timeoutMs) {
  while (espSerial.available()) espSerial.read();  // drain stale data
  espSerial.println(cmd);

  String response = "";
  unsigned long start = millis();
  while (millis() - start < timeoutMs) {
    while (espSerial.available()) {
      response += (char)espSerial.read();
    }
    if (response.indexOf(expect) != -1) return true;
  }
  return false;  // timeout
}
```

### SoftwareSerial limitations

The Uno has one hardware UART (pins 0 and 1), which is occupied by the USB serial connection to the PC. The ESP-01S therefore gets a software serial port on D2 and D3.

`SoftwareSerial` on the Uno is reliable up to around 57600 baud but becomes error-prone at higher speeds because it uses interrupt-driven bit-banging that can be disrupted by other interrupts (including those from `millis()`). Most ESP-01S modules ship with AT firmware configured for 115200 baud, which is too fast for `SoftwareSerial`. The fix is to flash the ESP-01S with its baud rate set to 9600 using:

```
AT+UART_DEF=9600,8,1,0,0
```

This persists across power cycles and makes the serial link solid.

### Float formatting on AVR

The ATmega328P (Arduino Uno's processor) uses a reduced `printf` implementation that does not support `%f` float formatting by default. A naive `sprintf(buf, "{\"temp\":%.1f}", temp)` will produce garbage. The correct approach on AVR is `dtostrf`:

```cpp
char tempStr[8];
dtostrf(temp, 4, 1, tempStr);
String body = "{\"temp\":" + String(tempStr) + ",\"smoke\":" + String(smoke) + "}";
```

`dtostrf` is part of `avr-libc` and handles float-to-string conversion correctly on 8-bit AVR processors.

---

## Backend

### Why FastAPI on Vercel

Vercel is primarily known as a Node.js and Next.js platform, but it supports Python serverless functions through its runtime system. A `vercel.json` configuration file routes all requests to a single `api/index.py` entry point, which creates a FastAPI application and exposes it as an ASGI handler.

The advantages are significant for a project at this scale: zero infrastructure management, automatic HTTPS, global CDN, and a generous free tier. The main constraint is the serverless execution model — each function invocation is stateless, has a maximum execution time, and cannot hold a persistent database connection.

This is why Turso is used instead of a traditional hosted PostgreSQL or MySQL database. Turso's LibSQL client communicates over HTTP, which is exactly what serverless functions need.

### Database design

The schema has three tables.

`system_state` holds a single row — the current status of the sensor system. It is updated on every incoming reading and polled by the dashboard every two seconds.

`incidents` records fire events. Each row has a start time, an end time (the literal string `'Active'` while the incident is ongoing), and the peak temperature reached during the event. The application queries `WHERE end_time = 'Active'` to find the current incident.

`burning_logs` is the raw sensor timeline — every reading ever taken, with a timestamp, temperature, smoke ADC value, and a foreign key to an incident. Readings taken during normal operation are stored with `incident_id = 0`. This design keeps ambient and incident data in the same table, making it easy to plot a continuous timeline without joining across tables.

### The incident state machine

Every incoming reading from the Arduino triggers the same sequence:

1. Read the current system status from `system_state`.
2. Compute the dynamic threshold (see next section).
3. Decide the new status using the threshold and hysteresis rules.
4. If status changed from safe to critical, create a new incident row.
5. If currently critical, look up the active incident and update peak temperature if the current reading is higher.
6. If status changed from critical to safe, close the active incident by setting `end_time`.
7. Write the reading to `burning_logs` with the correct `incident_id`.
8. Update `system_state`.

Step 7 contains a bug that existed in the original codebase and is worth describing explicitly because it is subtle. The variable `active_inc_id` was initialized to `0` at the top of the function and was supposed to be updated when an active incident was found. It was not. The code that retrieved the real incident ID stored the result in a local variable `inc_id`, but the insert into `burning_logs` still referenced `active_inc_id`, which remained `0`. Every log entry was therefore stored under incident #0, and the per-incident graph always showed no data. The fix is a single variable name change, but finding it required tracing the full data flow.

### Dynamic Moving Average threshold

A fixed temperature threshold is fragile. A lab that normally runs at 28°C and a lab that normally runs at 35°C need different alert thresholds. A system configured for a cool room will false-alarm in summer.

The dynamic threshold solves this by computing the alert level from recent history:

```
threshold = AVG(last N readings where temp < current_threshold) + offset
```

The filter `temp < current_threshold` is critical. During a fire, temperatures rise well above normal. If those readings were included in the moving average, the threshold would drift upward, making the system less sensitive over time. By excluding readings above the current threshold, the average tracks only the normal ambient temperature.

`N` defaults to 10 (a 20-second window at 2s intervals). The offset defaults to 10°C, meaning the threshold sits 10 degrees above the recent ambient temperature.

### Hysteresis

Without hysteresis, a sensor reading that oscillates around the threshold causes the system to toggle between safe and critical on every reading — creating false incidents, fragmented log entries, and spurious alerts.

Hysteresis fixes this with asymmetric transition rules:

- The system enters `critical` when `temp > threshold` OR `smoke > smoke_limit`.
- The system returns to `safe` only when `temp ≤ threshold − hysteresis` AND `smoke ≤ smoke_limit`.

With a hysteresis value of 2°C, a sensor that wavers between 44.8°C and 45.2°C around a 45°C threshold will not toggle. Once triggered, it stays critical until temperature drops below 43°C.

The `hysteresis` key existed in the original `config.toml` but was never read by the application. The logic was simply missing.

### Server-side SVG charts

The graph endpoint renders the temperature and smoke data as SVG markup, computed entirely in Python and returned as part of an HTML fragment. There is no charting library. There is no JavaScript on the client side that draws anything.

This is an unusual choice but it works well in this context. SVG is text, which means it compresses well, caches well, and renders instantly without a JavaScript parse-and-execute cycle. The server has all the data it needs to compute coordinates, and the resulting markup is around 3–5KB for a typical incident graph.

The coordinate math is straightforward: the chart has fixed padding on all four sides, the temperature range is mapped linearly to the vertical axis with a minimum span enforced to prevent pathological scaling on flat data, and smoke values are rendered as semi-transparent bars against a right-hand axis.

One removed piece of dead code: a variable `mid_idx` was calculated on every render but never used in the output. It survived through several refactors.

---

## Frontend

The dashboard is a single HTML file with no build step. Beer CSS provides the dark theme, card components, and responsive grid. HTMX handles all the dynamic behaviour.

HTMX works by annotating HTML elements with attributes that describe server requests. An element with `hx-get="/api/status" hx-trigger="every 2s" hx-swap="outerHTML"` will fire a GET request to `/api/status` every two seconds and replace itself with the response. The server returns an HTML fragment — not JSON, not a full page — and HTMX drops it into the DOM.

This pattern is well suited to dashboard-style UIs where you want live updates without the complexity of a JavaScript state management layer. The server is the source of truth. The client is a thin rendering surface.

### The broken pagination

The original history section used a paginated table. The server returned the table body as one HTMX fragment and a separate pagination control as a second fragment using HTMX's out-of-band swap mechanism (`hx-swap-oob`). The idea was that both elements would update together — the table rows for the current page and the pagination buttons highlighting the current page number.

Out-of-band swaps require that the target element already exists in the DOM with a matching `id`. If it does not, the swap silently fails. In practice the pagination buttons were not rendering and the table rows were not appearing, making the history section look blank despite data being present in the database.

The fix was to remove the pagination entirely and replace it with a single scrollable `<div>` wrapping the table. The server returns a simple `<tbody>` with all rows (capped at 100), the HTMX trigger refreshes it every 10 seconds, and there is no OOB swap in sight. The history section now works reliably.

---

## What was fixed

To summarise the changes made during the review:

**Critical — burning_logs always stored under incident_id = 0.** The `active_inc_id` variable was initialized to 0 and never updated. All log rows landed in the ambient bucket, making per-incident graphs empty. Fixed by assigning the resolved `inc_id` to `active_inc_id` after the database lookup.

**Critical — peak temperature update targeted the wrong incident.** The same variable confusion caused the `UPDATE incidents SET peak_temp` query to always target incident #0, which does not exist. Peak temperatures were never recorded. Fixed alongside the above.

**Hysteresis config value was defined but ignored.** The threshold logic treated every crossing identically regardless of direction, causing flicker near the boundary. Fixed by applying asymmetric transition rules using the configured hysteresis value.

**Smoke labeled as PPM.** Raw MQ-2 ADC output is not PPM. Relabeled throughout the UI and graph axis.

**Broken history pagination.** Replaced with a scrollable table that avoids the OOB swap mechanism entirely.

**Unhandled ValueError on non-integer incident ID.** Added a try/except around the `int(incident_id)` call in the graph endpoint, returning a 400 instead of a 500.

**Dead code removed.** The `mid_idx` variable in the graph renderer was calculated and discarded. Removed.

**Unused dependency removed.** `requirements.txt` listed the `toml` package, but the code uses `tomllib` from the Python 3.11 standard library. Removed.

**Arduino firmware written from scratch.** The original `arduno.ino` contained only empty `setup()` and `loop()` functions. The complete firmware was written for the Arduino Uno + ESP-01S combination, covering WiFi connection, AT command management, sensor reading, float formatting on AVR, and HTTPS POST to the Vercel API.

---

## Reflections

A few things stand out looking back at the build.

The serverless + edge database combination is genuinely good for IoT dashboards at this scale. Vercel and Turso together give you a globally distributed backend for free, with no servers to manage and no database connections to pool. The HTTP-based database client is a feature, not a limitation.

HTMX is a strong fit for this kind of project. The dashboard is essentially a collection of polled server-rendered fragments. HTMX makes that pattern trivially easy to implement, and keeping the rendering on the server means the SVG chart logic, the threshold state, and the incident history all live in one place (Python), rather than being split between the server (data) and the client (presentation).

The ESP-01S + Arduino Uno combination is more friction than a standalone ESP32 but teaches you things you would otherwise never encounter — AT command protocols, serial voltage levels, AVR float handling. For a project where learning is part of the point, that friction is worthwhile.

The Dynamic Moving Average threshold is the most interesting piece of the system algorithmically. The detail that fire-period values must be excluded from the window to prevent threshold drift is easy to miss and easy to get wrong. Getting it right means the system adapts to environmental changes without becoming less sensitive during the events it is supposed to detect.
