#include <Arduino.h>   // needed for PlatformIO
#include <Mesh.h>
#include <helpers/LowBatteryBootGuard.h>

#include "MyMesh.h"

#ifdef DISPLAY_CLASS
  #include "UITask.h"
  static UITask ui_task(board, display);
#endif

StdRNG fast_rng;
SimpleMeshTables tables;

MyMesh the_mesh(board, radio_driver, *new ArduinoMillis(), fast_rng, rtc_clock, tables);

void halt() {
  while (1) ;
}

static char command[160];
static unsigned long next_runtime_lowbat_check = 0;
static unsigned long next_atlas_observer_export = 0;

#define ATLAS_OBSERVER_EXPORT_INTERVAL_MS 5000UL

// For power saving
unsigned long lastActive = 0; // mark last active time
unsigned long nextSleepinSecs = 120; // next sleep in seconds. The first sleep (if enabled) is after 2 minutes from boot
static uint8_t lastPowerSkipReason = 0;

#if defined(PIN_USER_BTN) && defined(_SEEED_SENSECAP_SOLAR_H_)
static unsigned long userBtnDownAt = 0;
#define USER_BTN_HOLD_OFF_MILLIS 1500
#endif

void setup() {
  Serial.begin(115200);
  delay(1000);

  board.begin();

#if defined(MESH_DEBUG) && defined(NRF52_PLATFORM)
  // give some extra time for serial to settle so
  // boot debug messages can be seen on terminal
  delay(5000);
#endif

#ifdef DISPLAY_CLASS
  if (display.begin()) {
    display.startFrame();
    display.setCursor(0, 0);
    display.print("Please wait...");
    display.endFrame();
  }
#endif

  if (!radio_init()) {
    MESH_DEBUG_PRINTLN("Radio init failed!");
    halt();
  }

  fast_rng.begin(radio_driver.getRngSeed());

  FILESYSTEM* fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  InternalFS.begin();
  fs = &InternalFS;
  IdentityStore store(InternalFS, "");
#elif defined(ESP32)
  SPIFFS.begin(true);
  fs = &SPIFFS;
  IdentityStore store(SPIFFS, "/identity");
#elif defined(RP2040_PLATFORM)
  LittleFS.begin();
  fs = &LittleFS;
  IdentityStore store(LittleFS, "/identity");
  store.begin();
#else
  #error "need to define filesystem"
#endif
  if (!store.load("_main", the_mesh.self_id)) {
    MESH_DEBUG_PRINTLN("Generating new keypair");
    the_mesh.self_id = radio_new_identity();   // create new random identity
    int count = 0;
    while (count < 10 && (the_mesh.self_id.pub_key[0] == 0x00 || the_mesh.self_id.pub_key[0] == 0xFF)) {  // reserved id hashes
      the_mesh.self_id = radio_new_identity(); count++;
    }
    store.save("_main", the_mesh.self_id);
  }

  Serial.print("Repeater ID: ");
  mesh::Utils::printHex(Serial, the_mesh.self_id.pub_key, PUB_KEY_SIZE); Serial.println();

  command[0] = 0;

  sensors.begin();

  the_mesh.begin(fs);
  the_mesh.recordStartupWakeReason();

#ifdef DISPLAY_CLASS
  ui_task.begin(the_mesh.getNodePrefs(), FIRMWARE_BUILD_DATE, FIRMWARE_VERSION);
#endif

  // send out initial zero hop Advertisement to the mesh
#if ENABLE_ADVERT_ON_BOOT == 1
  the_mesh.sendSelfAdvertisement(16000, false);
#endif

  board.onBootComplete();
}

void loop() {
  int len = strlen(command);
  bool command_ready = false;
  while (Serial.available() && len < sizeof(command)-1) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (len == 0) {
        continue;
      }
      command[len] = 0;
      Serial.print('\n');
      command_ready = true;
      break;
    }
    command[len++] = c;
    command[len] = 0;
    Serial.print(c);
  }
  if (len == sizeof(command)-1) {  // command buffer full
    command[sizeof(command)-1] = 0;
    command_ready = true;
  }

  if (command_ready) {  // received complete line
    char reply[768];
    the_mesh.handleCommand(0, command, reply);  // NOTE: there is no sender_timestamp via serial!
    if (reply[0]) {
      if (reply[0] == '{') {
        Serial.println(reply);
      } else {
        Serial.print("  -> "); Serial.println(reply);
      }
    }

    command[0] = 0;  // reset command buffer
  }

#if defined(PIN_USER_BTN) && defined(_SEEED_SENSECAP_SOLAR_H_)
  // Hold the user button to power off the SenseCAP Solar repeater.
  int btnState = digitalRead(PIN_USER_BTN);
  if (btnState == LOW) {
    if (userBtnDownAt == 0) {
      userBtnDownAt = millis();
    } else if ((unsigned long)(millis() - userBtnDownAt) >= USER_BTN_HOLD_OFF_MILLIS) {
      Serial.println("Powering off...");
      board.powerOff();  // does not return
    }
  } else {
    userBtnDownAt = 0;
  }
#endif

  the_mesh.loop();
  sensors.loop();
#ifdef DISPLAY_CLASS
  ui_task.loop();
#endif
  rtc_clock.tick();

  NodePrefs* prefs = the_mesh.getNodePrefs();
  if (next_runtime_lowbat_check == 0 || the_mesh.millisHasNowPassed(next_runtime_lowbat_check)) {
    next_runtime_lowbat_check = millis() + LOW_BAT_RUNTIME_CHECK_SECS * 1000UL;
    if (guardRuntimeLowBattery(board, prefs->low_bat_runtime_guard_enabled, prefs->low_bat_runtime_guard_mv,
                               prefs->low_bat_runtime_valid_min_mv, prefs->low_bat_runtime_retry_secs)) {
      return;
    }
  }

  if (prefs->atlas.export_enabled && command[0] == 0 &&
      (next_atlas_observer_export == 0 || (long)(millis() - next_atlas_observer_export) >= 0)) {
    char event[512];
    the_mesh.formatAtlasObserverReply(event);
    if (event[0] == '{') {
      Serial.println(event);
    }
    next_atlas_observer_export = millis() + ATLAS_OBSERVER_EXPORT_INTERVAL_MS;
  }

  if (prefs->powersaving_enabled) {
    uint8_t skipReason = 0;
    if (the_mesh.isBridgeActive()) {
      skipReason = 1;
    } else if (the_mesh.hasOutboundWork()) {
      skipReason = 2;
    }

    if (skipReason != 0) {
      if (skipReason != lastPowerSkipReason) {
        if (skipReason == 1) {
          the_mesh.recordSleepSkipBridgeActive();
        } else {
          the_mesh.recordSleepSkipPendingWork();
        }
      }
      lastPowerSkipReason = skipReason;
      return;
    }

    lastPowerSkipReason = 0;
    #if defined(NRF52_PLATFORM)
    the_mesh.recordSleepAttempt();
    board.sleep(1800); // nrf ignores seconds param, sleeps whenever possible
    #else
    if (the_mesh.millisHasNowPassed(lastActive + nextSleepinSecs * 1000)) { // To check if it is time to sleep
      the_mesh.recordSleepAttempt();
      board.sleep(1800);             // To sleep. Wake up after 30 minutes or when receiving a LoRa packet
      lastActive = millis();
      nextSleepinSecs = 5;  // Default: To work for 5s and sleep again
    } else {
      nextSleepinSecs += 5; // When there is pending work, to work another 5s
    }
    #endif
  } else {
    lastPowerSkipReason = 0;
  }
}
