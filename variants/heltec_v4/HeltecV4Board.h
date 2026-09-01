#pragma once

#include <Arduino.h>
#include <helpers/RefCountedDigitalPin.h>
#include <helpers/ESP32Board.h>
#include "LoRaFEMControl.h"

#ifndef ADC_MULTIPLIER
  #define ADC_MULTIPLIER 5.42
#endif

class HeltecV4Board : public ESP32Board {

protected:
  float adc_mult = ADC_MULTIPLIER;

public:
  RefCountedDigitalPin periph_power;
  LoRaFEMControl loRaFEMControl;
  HeltecV4Board() : periph_power(PIN_VEXT_EN,PIN_VEXT_EN_ACTIVE) { }

  void begin();
  void onBeforeTransmit(void) override;
  void onAfterTransmit(void) override;
  void powerOff() override;
  bool setLoRaFemLnaEnabled(bool enable);
  bool canControlLoRaFemLna() const;
  bool isLoRaFemLnaEnabled() const;
  uint16_t getBattMilliVolts() override;
  bool setAdcMultiplier(float multiplier) override {
    if (multiplier == 0.0f) {
      adc_mult = ADC_MULTIPLIER;
    } else {
      adc_mult = multiplier;
    }
    return true;
  }
  float getAdcMultiplier() const override { return adc_mult; }
  bool supportsFemRxGain() const override { return loRaFEMControl.isLnaCanControl(); }
  bool setFemRxGain(bool enabled) override {
    if (!loRaFEMControl.isLnaCanControl()) return false;
    loRaFEMControl.setLNAEnable(enabled);
    return true;
  }
  bool getFemRxGain() const override { return loRaFEMControl.getLNAEnable(); }
  const char* getManufacturerName() const override;
};
