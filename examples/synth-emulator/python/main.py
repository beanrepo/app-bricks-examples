# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.wave_generator import WaveGenerator
from arduino.app_peripherals.midi_keyboard import MIDIKeyboard
from arduino.app_utils import App, Logger
import logging

logger = Logger("synth-emulator", logging.DEBUG)

# Configuration - Balanced for quality and performance
SAMPLE_RATE = 44100  # CD quality (good compromise)

# Wave generator brick - handles audio generation and streaming
wave_gen = WaveGenerator(
    sample_rate=SAMPLE_RATE,
    wave_type="sine",
    block_duration=0.010,  # 10ms blocks - extreme low latency (441 frames @ 44.1kHz)
    attack=0.01,
    release=0.1,
    glide=0.0,  # No glide = instant pitch changes (eliminates CPU overhead)
)

# Set initial state
wave_gen.set_frequency(440.0)
wave_gen.set_amplitude(0.0)

# MIDI CC mapping configuration (stored in memory, configurable via UI)
# Maps MIDI CC numbers to synth parameters
cc_mapping = {
    # Default mappings (example for Akai MPK mini Plus)
    "waveform": None,  # CC for waveform selection (0-3 = sine/square/sawtooth/triangle)
    "attack": 1,  # CC1 = Modulation wheel → attack time
    "release": 2,  # CC2 → release time
    "glide": 3,  # CC3 → glide/portamento time
    "frequency": None,  # CC for frequency control
    "amplitude": 7,  # CC7 = Volume → amplitude
    "master_volume": 11,  # CC11 = Expression → master volume
}

# --- MIDI Keyboard support (optional) -----------------------------------------------
midi = None
midi_active_notes = []
midi_base_frequency = 440.0  # Base frequency for pitch bend tracking

try:
    available_midi = MIDIKeyboard.list_usb_devices()
    if available_midi:
        logger.info(f"MIDI devices found: {available_midi}")
        midi = MIDIKeyboard()
        logger.info(f"MIDI keyboard enabled: {midi.device_name}")
    else:
        logger.info("No MIDI devices found - web UI only mode")
except Exception as e:
    logger.warning(f"MIDI keyboard initialization failed: {e}")
    logger.info("Continuing with web UI only mode")

# --- Web UI and event handlers -----------------------------------------------------
ui = WebUI()


def on_connect(sid, data=None):
    """Send current synth state to newly connected client."""
    state = wave_gen.get_state()
    ui.send_message(
        "synth:state",
        {
            "frequency": state["frequency"],
            "amplitude": state["amplitude"],
            "waveform": state["wave_type"],
            "volume": state["volume"],
            "envelope": {
                "attack": wave_gen.attack,
                "release": wave_gen.release,
                "glide": wave_gen.glide,
            },
        },
        room=sid,
    )
    # Send CC mapping configuration
    ui.send_message("synth:cc_mapping", cc_mapping, room=sid)
    # Inform client if MIDI is available
    midi_status = {
        "available": midi is not None and midi.is_connected(),
        "device_name": midi.friendly_name if midi and hasattr(midi, "friendly_name") else None,
    }
    ui.send_message("synth:midi_status", midi_status, room=sid)


def on_set_frequency(sid, data=None):
    """Update synth frequency."""
    d = data or {}
    freq = float(d.get("frequency", 440.0))
    wave_gen.set_frequency(freq)
    logger.debug(f"Frequency set to {freq:.1f}Hz")
    ui.send_message("synth:state", {"frequency": freq})


def on_set_amplitude(sid, data=None):
    """Update synth amplitude."""
    d = data or {}
    amp = float(d.get("amplitude", 0.0))
    amp = max(0.0, min(1.0, amp))
    wave_gen.set_amplitude(amp)
    logger.debug(f"Amplitude set to {amp:.3f}")
    ui.send_message("synth:state", {"amplitude": amp})


def on_set_waveform(sid, data=None):
    """Change waveform type."""
    d = data or {}
    waveform = d.get("waveform", "sine")
    valid_waveforms = ["sine", "square", "sawtooth", "triangle"]
    if waveform in valid_waveforms:
        wave_gen.set_wave_type(waveform)
        logger.info(f"Waveform changed to: {waveform}")
        ui.send_message("synth:state", {"waveform": waveform})
    else:
        logger.warning(f"Invalid waveform: {waveform}")


def on_set_envelope(sid, data=None):
    """Update envelope parameters (attack, release, glide)."""
    d = data or {}
    attack = d.get("attack")
    release = d.get("release")
    glide = d.get("glide")

    # Convert from milliseconds to seconds if needed
    if attack is not None:
        attack = float(attack) / 1000.0
    if release is not None:
        release = float(release) / 1000.0
    if glide is not None:
        glide = float(glide) / 1000.0

    wave_gen.set_envelope_params(attack=attack, release=release, glide=glide)
    logger.debug(f"Envelope updated: attack={attack}, release={release}, glide={glide}")

    # Send confirmation
    ui.send_message(
        "synth:state",
        {
            "envelope": {
                "attack": wave_gen.attack * 1000,
                "release": wave_gen.release * 1000,
                "glide": wave_gen.glide * 1000,
            }
        },
    )


def on_set_volume(sid, data=None):
    """Set master volume."""
    d = data or {}
    volume = int(d.get("volume", 100))
    volume = max(0, min(100, volume))
    wave_gen.set_volume(volume)
    logger.debug(f"Master volume set to {volume}%")
    ui.send_message("synth:state", {"volume": volume})


def on_note_on(sid, data=None):
    """Trigger note via web UI (keyboard emulation)."""
    d = data or {}
    note = int(d.get("note", 60))  # MIDI note number
    velocity = int(d.get("velocity", 100))

    freq = MIDIKeyboard.note_to_frequency(note)
    amp = velocity / 127.0

    wave_gen.set_frequency(freq)
    wave_gen.set_amplitude(amp)

    logger.info(f"Web UI Note ON: {note} ({freq:.1f}Hz) vel={velocity}")
    ui.send_message("synth:state", {"frequency": freq, "amplitude": amp})


def on_note_off(sid, data=None):
    """Release note via web UI."""
    wave_gen.set_amplitude(0.0)
    logger.info("Web UI Note OFF")
    ui.send_message("synth:state", {"amplitude": 0.0})


def on_update_cc_mapping(sid, data=None):
    """Update MIDI CC mapping configuration."""
    global cc_mapping
    d = data or {}
    param = d.get("param")  # e.g., "attack", "waveform"
    cc_num = d.get("cc")  # CC number or None to clear

    if param in cc_mapping:
        cc_mapping[param] = cc_num if cc_num is not None else None
        logger.info(f"CC mapping updated: {param} → CC{cc_num}")
        ui.send_message("synth:cc_mapping", cc_mapping)
    else:
        logger.warning(f"Invalid parameter for CC mapping: {param}")


def on_learn_cc(sid, data=None):
    """Enter CC learn mode for a parameter."""
    d = data or {}
    param = d.get("param")
    logger.info(f"CC learn mode activated for: {param}")
    # The next CC message will be mapped to this parameter
    # This is handled in the MIDI CC callback below
    ui.send_message("synth:cc_learn", {"param": param})


# Register UI event handlers
ui.on_connect(on_connect)
ui.on_message("synth:set_frequency", on_set_frequency)
ui.on_message("synth:set_amplitude", on_set_amplitude)
ui.on_message("synth:set_waveform", on_set_waveform)
ui.on_message("synth:set_envelope", on_set_envelope)
ui.on_message("synth:set_volume", on_set_volume)
ui.on_message("synth:note_on", on_note_on)
ui.on_message("synth:note_off", on_note_off)
ui.on_message("synth:update_cc_mapping", on_update_cc_mapping)
ui.on_message("synth:learn_cc", on_learn_cc)


# --- MIDI event handlers (defined globally for hotplug support) --------------------

# Pitch bend throttling - process max every 25ms to reduce CPU load
import time

_last_pitch_bend_time = time.perf_counter()  # Initialize with current time
_PITCH_BEND_THROTTLE_SEC = 0.025  # 25ms = aggressive throttling


def on_midi_note_on(note, velocity):
    """Handle MIDI note on."""
    global midi_base_frequency
    logger.info(f"🎹 MIDI Note ON: note={note} velocity={velocity}")
    midi_active_notes.append(note)
    freq = MIDIKeyboard.note_to_frequency(note)
    midi_base_frequency = freq  # Save base frequency for pitch wheel
    amp = velocity / 127.0

    wave_gen.set_frequency(freq)
    # Re-trigger amplitude to apply attack envelope
    wave_gen.set_amplitude(0.0)  # Reset to trigger attack
    wave_gen.set_amplitude(amp)

    # Broadcast to web clients
    ui.send_message("synth:state", {"frequency": freq, "amplitude": amp, "source": "midi"})


def on_midi_note_off(note, velocity):
    """Handle MIDI note off with last-note priority."""
    global midi_base_frequency
    if note in midi_active_notes:
        midi_active_notes.remove(note)

    if midi_active_notes:
        # Play most recent note
        last_note = midi_active_notes[-1]
        freq = MIDIKeyboard.note_to_frequency(last_note)
        midi_base_frequency = freq  # Update base frequency for pitch wheel
        wave_gen.set_frequency(freq)
        logger.debug(f"MIDI Note OFF: {note} → switching to {last_note}")
        ui.send_message("synth:state", {"frequency": freq, "amplitude": wave_gen._current_amp, "source": "midi"})
    else:
        # No notes pressed, fade out
        wave_gen.set_amplitude(0.0)
        logger.debug(f"MIDI Note OFF: {note} → silence")
        ui.send_message("synth:state", {"amplitude": 0.0, "source": "midi"})


def on_midi_pitch_bend(value):
    """Handle pitch bend with spring-back (±2 semitones = ±1 tono).

    Pitch wheel sends absolute value 0-16383 with center at 8192.
    When released, wheel springs back to center, sending all intermediate values.
    Throttled to max every 15ms to prevent CPU overload and buffer underruns.
    """
    global midi_base_frequency, _last_pitch_bend_time

    # Throttle: skip if less than 15ms since last pitch bend (use perf_counter for precision)
    current_time = time.perf_counter()
    if (current_time - _last_pitch_bend_time) < _PITCH_BEND_THROTTLE_SEC:
        return  # Skip this event
    _last_pitch_bend_time = current_time

    # Normalize to -1.0 to +1.0 (center = 0)
    normalized_bend = (value - 8192) / 8192.0
    # Apply ±2 semitones range (1 tone)
    bend_semitones = normalized_bend * 2.0
    bend_factor = 2.0 ** (bend_semitones / 12.0)
    new_freq = midi_base_frequency * bend_factor
    wave_gen.set_frequency(new_freq)
    logger.debug(f"MIDI Pitch bend: {value} (norm={normalized_bend:.2f}) → {new_freq:.1f}Hz (base={midi_base_frequency:.1f}Hz)")
    ui.send_message("synth:state", {"frequency": new_freq, "source": "midi"})


def on_midi_cc(control, value):
    """Handle MIDI CC based on mapping configuration."""
    logger.debug(f"🎹 MIDI CC{control} = {value}")

    # Check if this CC is mapped to any parameter
    for param, mapped_cc in cc_mapping.items():
        if mapped_cc == control:
            logger.info(f"MIDI CC{control} ({value}) → {param}")

            if param == "waveform":
                # Map CC value 0-127 to waveform index 0-3
                waveforms = ["sine", "square", "sawtooth", "triangle"]
                idx = min(3, int((value / 127.0) * 4))
                wave_gen.set_wave_type(waveforms[idx])
                ui.send_message("synth:state", {"waveform": waveforms[idx], "source": "midi"})

            elif param == "attack":
                attack_ms = (value / 127.0) * 500  # 0-500ms
                wave_gen.set_envelope_params(attack=attack_ms / 1000.0)
                ui.send_message("synth:state", {"attack": attack_ms, "envelope": {"attack": attack_ms}, "source": "midi"})

            elif param == "release":
                release_ms = (value / 127.0) * 1000  # 0-1000ms
                wave_gen.set_envelope_params(release=release_ms / 1000.0)
                ui.send_message("synth:state", {"release": release_ms, "envelope": {"release": release_ms}, "source": "midi"})

            elif param == "glide":
                glide_ms = (value / 127.0) * 200  # 0-200ms
                wave_gen.set_envelope_params(glide=glide_ms / 1000.0)
                ui.send_message("synth:state", {"glide": glide_ms, "envelope": {"glide": glide_ms}, "source": "midi"})

            elif param == "amplitude":
                amp = value / 127.0
                if midi_active_notes:  # Only if notes are playing
                    wave_gen.set_amplitude(amp)
                    ui.send_message("synth:state", {"amplitude": amp, "source": "midi"})

            elif param == "master_volume":
                volume = int((value / 127.0) * 100)
                wave_gen.set_volume(volume)
                ui.send_message("synth:state", {"volume": volume, "source": "midi"})

            elif param == "frequency":
                # Map CC value to frequency range (e.g., 100-2000 Hz)
                freq = 100 + (value / 127.0) * 1900
                wave_gen.set_frequency(freq)
                ui.send_message("synth:state", {"frequency": freq, "source": "midi"})

    # Also broadcast raw CC for monitoring/learning
    ui.send_message("synth:midi_cc", {"control": control, "value": value})


# --- Register MIDI callbacks if device available -----------------------------------
if midi:
    midi.on_note_on(on_midi_note_on)
    midi.on_note_off(on_midi_note_off)
    midi.on_pitch_bend(on_midi_pitch_bend)
    midi.on_control_change(on_midi_cc)

    # Start MIDI listener
    midi.start()
    logger.info("MIDI keyboard ready with CC mapping support")

# --- MIDI Hotplug Detection Thread ---------------------------------------------


def midi_hotplug_monitor():
    """Monitor MIDI connection status and notify clients."""
    import time

    global midi

    last_status = midi is not None and midi.is_connected()
    logger.info(f"Hotplug monitor started, initial status: {last_status}")
    check_counter = 0

    while True:
        time.sleep(2)  # Check every 2 seconds
        check_counter += 1

        if midi is None:
            # No MIDI initialized, check if device appeared
            available_midi = MIDIKeyboard.list_usb_devices()
            if available_midi:
                logger.info(f"MIDI device detected: {[d['friendly_name'] for d in available_midi]}")
                try:
                    midi = MIDIKeyboard()
                    logger.info(f"MIDI keyboard reconnected: {midi.friendly_name}")

                    # Register callbacks again
                    midi.on_note_on(on_midi_note_on)
                    midi.on_note_off(on_midi_note_off)
                    midi.on_pitch_bend(on_midi_pitch_bend)
                    midi.on_control_change(on_midi_cc)
                    midi.start()

                    # Notify clients
                    midi_status = {"available": True, "device_name": midi.friendly_name}
                    ui.send_message("synth:midi_status", midi_status)
                    last_status = True
                    logger.info("MIDI reconnection complete, status broadcast sent")
                    check_counter = 0  # Reset counter after reconnection
                except Exception as e:
                    logger.error(f"Failed to reconnect MIDI: {e}", exc_info=True)
                    midi = None
            # Only log "no devices" every 30 checks (60 seconds) to reduce spam
            elif check_counter % 30 == 0:
                logger.debug("No MIDI devices detected during scan")
        else:
            # MIDI was initialized, check if still connected
            current_status = midi.is_connected()
            if current_status != last_status:
                logger.warning(f"MIDI status changed: {last_status} → {current_status}")
                if not current_status:
                    # Disconnected
                    logger.error("MIDI device disconnected, notifying clients")
                    try:
                        midi.stop()
                    except Exception as e:
                        logger.error(f"Error stopping MIDI: {e}")
                    midi = None
                    ui.send_message("synth:midi_status", {"available": False, "device_name": None})
                    logger.info("Disconnection status broadcast sent")
                    check_counter = 0  # Reset counter after disconnection
                last_status = current_status
            # Only log "still connected" every 30 checks (60 seconds)
            elif check_counter % 30 == 0:
                logger.debug(f"MIDI status periodic check: connected={current_status}")


import threading

hotplug_thread = threading.Thread(target=midi_hotplug_monitor, daemon=True, name="MIDI-Hotplug-Monitor")
hotplug_thread.start()
logger.info("MIDI hotplug monitor started")

# Run the app
App.run()
