# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_utils import *
from arduino.app_bricks.wave_generator import WaveGenerator

logger = Logger("ultrasonic-wave")

MIN_FREQ_HZ = 220.0
MAX_FREQ_HZ = 880.0
TONE_AMPLITUDE = 0.35
MASTER_VOLUME = 80

wave = WaveGenerator(
    wave_type="sine",
    attack=0.01,
    release=0.04,
    glide=0.03,
)

wave.volume = MASTER_VOLUME
wave.frequency = 440.0
wave.amplitude = 0.0

latest_distance_cm = -1.0
latest_proximity = 0.0


def map_proximity_to_frequency(proximity: float) -> float:
    proximity = max(0.0, min(1.0, proximity))
    return MIN_FREQ_HZ + (MAX_FREQ_HZ - MIN_FREQ_HZ) * proximity


def set_sensor(distance_cm, proximity):
    global latest_distance_cm, latest_proximity

    try:
        distance_cm = float(distance_cm)
        proximity = max(0.0, min(1.0, float(proximity)))
    except Exception as e:
        logger.error(f"Invalid sensor payload: {e}")
        wave.amplitude = 0.0
        return False

    latest_distance_cm = distance_cm
    latest_proximity = proximity

    # Nessuna eco valida -> silenzio
    if distance_cm < 0.0:
        wave.amplitude = 0.0
        return True

    # Modula un solo parametro: la frequenza
    wave.frequency = map_proximity_to_frequency(proximity)
    wave.amplitude = TONE_AMPLITUDE

    logger.debug(
        f"distance={latest_distance_cm:.1f} cm "
        f"proximity={latest_proximity:.3f} "
        f"freq={wave.frequency:.1f} Hz"
    )
    return True


Bridge.provide("set_sensor", set_sensor)

App.run()