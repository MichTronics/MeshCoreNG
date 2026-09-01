#pragma once

#include <cstdint>
#include <stddef.h>

namespace mesh {

class Radio {
public:
  virtual ~Radio() = default;
  virtual int recvRaw(uint8_t*, int) { return 0; }
  virtual uint32_t getEstAirtimeFor(int) { return 10; }
  virtual float packetScore(float, int) { return 0.0f; }
  virtual bool startSendRaw(const uint8_t*, int) { return true; }
  virtual bool isSendComplete() { return true; }
  virtual void onSendFinished() {}
  virtual void loop() {}
  virtual int getNoiseFloor() const { return -120; }
  virtual void triggerNoiseFloorCalibrate(int) {}
  virtual void setCADEnabled(bool) {}
  virtual void resetAGC() {}
  virtual bool isInRecvMode() const { return false; }
  virtual bool isReceiving() { return false; }
  virtual float getLastRSSI() const { return 0.0f; }
  virtual float getLastSNR() const { return 0.0f; }
  virtual uint32_t getPacketsRecvErrors() const { return 0; }
};

class MainBoard {
public:
  virtual ~MainBoard() = default;
  virtual uint16_t getBattMilliVolts() { return 4200; }
  virtual float getMCUTemperature() { return 25.0f; }
  virtual bool setAdcMultiplier(float) { return false; }
  virtual float getAdcMultiplier() const { return 0.0f; }
  virtual bool supportsFemRxGain() const { return false; }
  virtual bool setFemRxGain(bool) { return false; }
  virtual bool getFemRxGain() const { return false; }
  virtual const char* getManufacturerName() const { return "mock-board"; }
  virtual void onBeforeTransmit() {}
  virtual void onAfterTransmit() {}
  virtual void reboot() {}
  virtual void powerOff() {}
  virtual void onBootComplete() {}
  virtual uint32_t getIRQGpio() { return 0; }
  virtual void sleep(uint32_t) {}
  virtual uint32_t getGpio() { return 0; }
  virtual void setGpio(uint32_t) {}
  virtual uint8_t getStartupReason() const { return 0; }
  virtual bool getBootloaderVersion(char*, size_t) { return false; }
  virtual bool startOTAUpdate(const char*, char[]) { return false; }
  virtual bool checkOnlineOTAUpdate(const char*, const char*, const char*, const char*, char[]) { return false; }
  virtual bool startOnlineOTAUpdate(const char*, const char*, const char*, const char*, char[]) { return false; }
  virtual bool isExternalPowered() { return false; }
  virtual uint16_t getBootVoltage() { return 0; }
  virtual uint32_t getResetReason() const { return 0; }
  virtual const char* getResetReasonString(uint32_t) { return "Not available"; }
  virtual uint8_t getShutdownReason() const { return 0; }
  virtual const char* getShutdownReasonString(uint8_t) { return "Not available"; }
};

}
