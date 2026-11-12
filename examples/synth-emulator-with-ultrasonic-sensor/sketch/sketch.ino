// SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
//
// SPDX-License-Identifier: MPL-2.0

#include <Arduino_RouterBridge.h>

// HC-SR04
const uint8_t PIN_ECHO = D4;
const uint8_t PIN_TRIG = D5;

// Sensor settings
const unsigned long ECHO_TIMEOUT_US = 30000UL;
const int SENSOR_MIN_CM = 2;
const int SENSOR_MAX_CM = 450;

// Musical control range
const int DISTANCE_MIN_CM = 2;
const int DISTANCE_MAX_100_PERCENT_CM = 50;

// Smoothing
const float FILTER_ALPHA = 0.25f;

// Update rate toward Python
const unsigned long SEND_INTERVAL_MS = 40;

float filteredCm = 0.0f;
bool hasValidReading = false;
unsigned long lastSendMs = 0;

float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

float readDistanceCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);

  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  unsigned long duration = pulseIn(PIN_ECHO, HIGH, ECHO_TIMEOUT_US);

  if (duration == 0) {
    return -1.0f;
  }

  float cm = duration / 58.0f;

  if (cm < SENSOR_MIN_CM || cm > SENSOR_MAX_CM) {
    return -1.0f;
  }

  return cm;
}

bool pushSensorState(float distanceCm, float proximity) {
  bool accepted = false;
  bool ok = Bridge.call("set_sensor", distanceCm, proximity).result(accepted);
  return ok && accepted;
}

void setup() {
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);

  digitalWrite(PIN_TRIG, LOW);

  Bridge.begin();
}

void loop() {
  unsigned long now = millis();
  if (now - lastSendMs < SEND_INTERVAL_MS) {
    delay(1);
    return;
  }
  lastSendMs = now;

  float rawCm = readDistanceCm();

  float distanceCm = -1.0f;
  float proximity = 0.0f;

  if (rawCm > 0.0f) {
    if (!hasValidReading) {
      filteredCm = rawCm;
      hasValidReading = true;
    } else {
      filteredCm = FILTER_ALPHA * rawCm + (1.0f - FILTER_ALPHA) * filteredCm;
    }

    distanceCm = filteredCm;

    float clipped = clampf(filteredCm, DISTANCE_MIN_CM, DISTANCE_MAX_100_PERCENT_CM);
    float distancePct = (clipped - DISTANCE_MIN_CM) /
                        (float)(DISTANCE_MAX_100_PERCENT_CM - DISTANCE_MIN_CM);

    proximity = 1.0f - distancePct;   // 1 = vicino, 0 = lontano
  } else {
    hasValidReading = false;
    distanceCm = -1.0f;
    proximity = 0.0f;
  }

  pushSensorState(distanceCm, proximity);
}