#ifdef ESP_PLATFORM

#include "ESP32Board.h"
#include "target.h"

#include <WiFi.h>
#include <stdarg.h>

void ESP32Board::powerOff() {
  enterDeepSleep(0); // Do not wakeup
}

void ESP32Board::enterDeepSleep(uint32_t secs) {
  // Power off the display if any
#ifdef DISPLAY_CLASS
  display.turnOff();
#endif

  // Power off LoRa
  radio_driver.powerOff();

  // Keep LoRa inactive during deepsleep
  digitalWrite(P_LORA_NSS, HIGH);
#if defined(CONFIG_IDF_TARGET_ESP32C3) || defined(CONFIG_IDF_TARGET_ESP32C6)
  gpio_hold_en((gpio_num_t)P_LORA_NSS);
#else
  rtc_gpio_hold_en((gpio_num_t)P_LORA_NSS);
#endif

  // Power off GPS if any
  if (sensors.getLocationProvider() != NULL) {
    sensors.getLocationProvider()->stop();
  }

  // Flush serial buffers
  Serial.flush();
  delay(100);

  // Clear stale wakeup sources to avoid ghost wakeup
  // This is required when Power Management and automatic lightsleep are enabled
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);

  if (secs > 0) {
    esp_sleep_enable_timer_wakeup(secs * 1000000ULL);
  }

  // Finally set ESP32 into deepsleep
  esp_deep_sleep_start(); // CPU halts here and never returns!
}

#if defined(ADMIN_PASSWORD)
#include <HTTPClient.h>
#include <Update.h>
#include <WiFiClientSecure.h>

#ifndef OTA_MANIFEST_URL
#define OTA_MANIFEST_URL "https://michtronics.github.io/MeshCoreNG/flasher/ota-manifest.txt"
#endif

struct Esp32OtaAsset {
  String version;
  String url;
  String name;
  int size;
};

static void otaStatus(const char* status) {
  Serial.printf("OTA: %s\n", status);
  MESH_DEBUG_PRINTLN("OTA: %s", status);
}

static void otaStatusf(const char* fmt, ...) {
  char msg[120];
  va_list args;
  va_start(args, fmt);
  vsnprintf(msg, sizeof(msg), fmt, args);
  va_end(args);
  otaStatus(msg);
}

static bool connectOtaWifi(const char* ssid, const char* password, char reply[]) {
  if (!ssid || ssid[0] == 0) {
    strcpy(reply, "Error: set wifi.ssid first");
    return false;
  }

  if (WiFi.status() == WL_CONNECTED) {
    String current_ssid = WiFi.SSID();
    if (current_ssid.length() == 0 || current_ssid == ssid) {
      otaStatusf("wifi already connected ip=%s", WiFi.localIP().toString().c_str());
      return true;
    }
  }

  otaStatusf("wifi connecting to %s", ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  uint32_t start = millis();
  uint32_t last_status = 0;
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    if (millis() - last_status >= 2000) {
      last_status = millis();
      otaStatusf("wifi connecting %lus", (unsigned long)((millis() - start) / 1000));
    }
    delay(250);
  }
  if (WiFi.status() != WL_CONNECTED) {
    strcpy(reply, "Error: WiFi connect failed");
    otaStatus("wifi failed");
    return false;
  }
  otaStatusf("wifi connected ip=%s", WiFi.localIP().toString().c_str());
  return true;
}

static bool parseOtaLine(const String& line, const char* target, Esp32OtaAsset* asset) {
  if (line.length() == 0 || line[0] == '#') return false;
  int p1 = line.indexOf('|');
  int p2 = line.indexOf('|', p1 + 1);
  int p3 = line.indexOf('|', p2 + 1);
  int p4 = line.indexOf('|', p3 + 1);
  if (p1 <= 0 || p2 <= p1 || p3 <= p2 || p4 <= p3) return false;
  if (line.substring(0, p1) != target) return false;

  asset->version = line.substring(p1 + 1, p2);
  asset->size = line.substring(p2 + 1, p3).toInt();
  asset->url = line.substring(p3 + 1, p4);
  asset->name = line.substring(p4 + 1);
  asset->name.trim();
  return asset->url.length() > 0 && asset->size > 0;
}

static void readHttpBody(HTTPClient& http, String* body) {
  body->remove(0);
  int size = http.getSize();
  if (size > 0) body->reserve(size + 1);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buffer[512];
  uint32_t last_data = millis();
  while ((http.connected() || stream->available()) && (size <= 0 || body->length() < (uint32_t)size)) {
    int available = stream->available();
    if (available <= 0) {
      if (millis() - last_data > 15000) break;
      delay(10);
      continue;
    }

    int to_read = min(available, (int)sizeof(buffer));
    if (size > 0) {
      int remaining = size - (int)body->length();
      if (remaining <= 0) break;
      to_read = min(to_read, remaining);
    }

    int read_len = stream->readBytes(buffer, to_read);
    if (read_len > 0) {
      body->concat((const char*)buffer, read_len);
      last_data = millis();
    } else {
      delay(10);
    }
  }
}

static bool fetchOtaAsset(const char* target, Esp32OtaAsset* asset, char reply[]) {
  WiFiClientSecure client;
  client.setInsecure();

  String manifest_url = String(OTA_MANIFEST_URL);
  manifest_url += manifest_url.indexOf('?') >= 0 ? "&t=" : "?t=";
  manifest_url += String((uint32_t)millis());

  otaStatus("manifest fetching");
  HTTPClient http;
  http.useHTTP10(true);
  http.setTimeout(15000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  if (!http.begin(client, manifest_url)) {
    strcpy(reply, "Error: manifest begin failed");
    otaStatus("manifest begin failed");
    return false;
  }
  http.addHeader("Accept-Encoding", "identity");
  http.addHeader("Cache-Control", "no-cache");
  http.addHeader("Pragma", "no-cache");

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    snprintf(reply, 160, "Error: manifest HTTP %d", code);
    http.end();
    otaStatusf("manifest http %d", code);
    return false;
  }

  String body;
  readHttpBody(http, &body);
  int size = http.getSize();
  otaStatusf("manifest bytes=%u size=%d", (uint32_t)body.length(), size);
  if (body.length() == 0) {
    snprintf(reply, 160, "Error: manifest empty HTTP %d size=%d", code, size);
    http.end();
    otaStatusf("manifest empty size=%d", size);
    return false;
  }

  int start = 0;
  uint16_t line_count = 0;
  while (start < body.length()) {
    int end = body.indexOf('\n', start);
    if (end < 0) end = body.length();
    String line = body.substring(start, end);
    line.trim();
    if (line.length() > 0) line_count++;
    if (parseOtaLine(line, target, asset)) {
      http.end();
      otaStatusf("manifest ok latest=%s size=%u", asset->version.c_str(), (uint32_t)asset->size);
      return true;
    }
    start = end + 1;
  }

  http.end();
  snprintf(reply, 160, "Error: no OTA for %s (lines=%u)", target, (uint32_t)line_count);
  otaStatusf("manifest no ota for %s lines=%u", target, (uint32_t)line_count);
  return false;
}

static bool downloadAndInstallOta(const Esp32OtaAsset& asset, char reply[]) {
  WiFiClientSecure client;
  client.setInsecure();

  otaStatusf("firmware downloading %s", asset.version.c_str());
  HTTPClient http;
  http.setTimeout(30000);
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  if (!http.begin(client, asset.url)) {
    strcpy(reply, "Error: firmware begin failed");
    otaStatus("firmware begin failed");
    return false;
  }

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    snprintf(reply, 160, "Error: firmware HTTP %d", code);
    http.end();
    otaStatusf("firmware http %d", code);
    return false;
  }

  int size = http.getSize();
  if (size <= 0) size = asset.size;
  otaStatusf("flash begin size=%u", (uint32_t)size);
  if (!Update.begin(size)) {
    snprintf(reply, 160, "Error: OTA begin %s", Update.errorString());
    http.end();
    otaStatusf("flash begin failed %s", Update.errorString());
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  size_t written = 0;
  uint8_t buffer[1024];
  int last_percent = -1;
  uint32_t last_progress = 0;
  while (http.connected() && written < (size_t)size) {
    size_t available = stream->available();
    if (available == 0) {
      delay(10);
      if (millis() - last_progress > 10000) {
        otaStatusf("download waiting %u/%u", (uint32_t)written, (uint32_t)size);
        last_progress = millis();
      }
      continue;
    }

    size_t to_read = available > sizeof(buffer) ? sizeof(buffer) : available;
    int read_len = stream->readBytes(buffer, to_read);
    if (read_len <= 0) continue;

    size_t chunk_written = Update.write(buffer, read_len);
    written += chunk_written;
    if (chunk_written != (size_t)read_len) break;

    int percent = size > 0 ? (int)((written * 100U) / (size_t)size) : 0;
    if (percent != last_percent && (percent % 10 == 0 || millis() - last_progress >= 3000)) {
      last_percent = percent;
      last_progress = millis();
      otaStatusf("download/flash %d%% %u/%u", percent, (uint32_t)written, (uint32_t)size);
    }
  }

  otaStatus("flash verifying");
  bool ok = written == (size_t)size && Update.end(true);
  if (!ok) {
    snprintf(reply, 160, "Error: OTA write %u/%u %s", (uint32_t)written, (uint32_t)size, Update.errorString());
    http.end();
    otaStatusf("flash failed %u/%u %s", (uint32_t)written, (uint32_t)size, Update.errorString());
    return false;
  }

  http.end();
  snprintf(reply, 160, "OK: installed %s, reboot", asset.version.c_str());
  otaStatusf("installed %s, reboot required", asset.version.c_str());
  return true;
}

#endif

#if defined(ADMIN_PASSWORD) && !defined(DISABLE_WIFI_OTA) && \
    __has_include(<AsyncTCP.h>) && __has_include(<ESPAsyncWebServer.h>) && __has_include(<AsyncElegantOTA.h>)
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <AsyncElegantOTA.h>

#include <SPIFFS.h>

bool ESP32Board::startOTAUpdate(const char* id, char reply[]) {
  inhibit_sleep = true;   // prevent sleep during OTA
  WiFi.softAP("MeshCore-OTA", NULL);

  sprintf(reply, "Started: http://%s/update", WiFi.softAPIP().toString().c_str());
  MESH_DEBUG_PRINTLN("startOTAUpdate: %s", reply);

  static char id_buf[60];
  sprintf(id_buf, "%s (%s)", id, getManufacturerName());
  static char home_buf[90];
  sprintf(home_buf, "<H2>Hi! I am a MeshCore Repeater. ID: %s</H2>", id);

  AsyncWebServer* server = new AsyncWebServer(80);

  server->on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(200, "text/html", home_buf);
  });
  server->on("/log", HTTP_GET, [](AsyncWebServerRequest *request) {
    request->send(SPIFFS, "/packet_log", "text/plain");
  });

  AsyncElegantOTA.setID(id_buf);
  AsyncElegantOTA.begin(server);    // Start ElegantOTA
  server->begin();

  return true;
}

#else
bool ESP32Board::startOTAUpdate(const char* id, char reply[]) {
  return false; // not supported
}
#endif

#if defined(ADMIN_PASSWORD)
bool ESP32Board::checkOnlineOTAUpdate(const char* target, const char* current_version, const char* wifi_ssid, const char* wifi_password, char reply[]) {
  if (!connectOtaWifi(wifi_ssid, wifi_password, reply)) return false;

  Esp32OtaAsset asset;
  if (!fetchOtaAsset(target, &asset, reply)) return false;

  bool update_available = asset.version != current_version;
  snprintf(reply, 160, "target=%s current=%s latest=%s update=%s size=%u",
           target, current_version, asset.version.c_str(), update_available ? "yes" : "no", (uint32_t)asset.size);
  return true;
}

bool ESP32Board::startOnlineOTAUpdate(const char* target, const char* current_version, const char* wifi_ssid, const char* wifi_password, char reply[]) {
  inhibit_sleep = true;
  otaStatusf("starting target=%s current=%s", target, current_version);
  if (!connectOtaWifi(wifi_ssid, wifi_password, reply)) return false;

  Esp32OtaAsset asset;
  if (!fetchOtaAsset(target, &asset, reply)) return false;
  if (asset.version == current_version) {
    snprintf(reply, 160, "OK: already %s", current_version);
    otaStatusf("already current %s", current_version);
    return true;
  }

  return downloadAndInstallOta(asset, reply);
}

#else

bool ESP32Board::checkOnlineOTAUpdate(const char* target, const char* current_version, const char* wifi_ssid, const char* wifi_password, char reply[]) {
  return false;
}

bool ESP32Board::startOnlineOTAUpdate(const char* target, const char* current_version, const char* wifi_ssid, const char* wifi_password, char reply[]) {
  return false;
}

#endif

#endif
