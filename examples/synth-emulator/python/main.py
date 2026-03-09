# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Logger
from midi_keyboard import MIDIKeyboard
from poly_wave_generator import PolyWaveGenerator
import logging

logger = Logger("synth-emulator", logging.DEBUG)

# Polyphonic wave generator (1–3 voices sharing a single speaker stream)
wave_gen = PolyWaveGenerator(
    wave_type="sine",
    attack=0.01,
    release=0.1,
    glide=0.0,
)

# MIDI CC mapping configuration (stored in memory, configurable via UI)
# Maps MIDI CC numbers to synth parameters
cc_mapping = {
    # Default mappings (example for Akai MPK mini Plus)
    "waveform": None,  # CC for waveform selection (0-3 = sine/square/sawtooth/triangle)
    "attack": 1,  # CC1 = Modulation wheel → attack time
    "decay": None,  # CC → decay time
    "sustain": None,  # CC → sustain level
    "release": 2,  # CC2 → release time
    "glide": 3,  # CC3 → glide/portamento time
    "frequency": None,  # CC for frequency control
    "amplitude": 7,  # CC7 = Volume → amplitude
    "master_volume": 11,  # CC11 = Expression → master volume
    "cutoff": None,  # CC → filter cutoff
    "resonance": None,  # CC → filter resonance
    "overdrive": None,  # CC → overdrive amount
    "tremolo_depth": None,  # CC → tremolo depth
    "tremolo_rate": None,  # CC → tremolo rate
    "delay_time": None,  # CC → delay time
    "delay_feedback": None,  # CC → delay feedback
    "reverb_wet": None,  # CC → reverb wet amount
}

# --- MIDI Keyboard support (optional) -----------------------------------------------
midi = None

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
    state = wave_gen.state
    ui.send_message(
        "synth:state",
        {
            "frequency": state["frequency"],
            "amplitude": state["amplitude"],
            "waveform": state["wave_type"],
            "volume": state["volume"],
            "voices": state["voices"],
            "envelope": {
                "attack": wave_gen.attack * 1000,
                "decay": wave_gen.decay * 1000,
                "sustain": wave_gen.sustain * 100,
                "release": wave_gen.release * 1000,
                "glide": wave_gen.glide * 1000,
            },
            "effects": {
                "cutoff": state["cutoff"],
                "resonance": state["resonance"] * 100,
                "overdrive": state["overdrive"] * 100,
                "tremolo_depth": state["tremolo_depth"] * 100,
                "tremolo_rate": state["tremolo_rate"],
                "delay_time": state["delay_time"] * 1000,
                "delay_feedback": state["delay_feedback"] * 100,
                "reverb_wet": state["reverb_wet"] * 100,
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
    wave_gen.frequency = freq
    logger.debug(f"Frequency set to {freq:.1f}Hz")
    ui.send_message("synth:state", {"frequency": freq})


def on_set_amplitude(sid, data=None):
    """Update synth amplitude."""
    d = data or {}
    amp = float(d.get("amplitude", 0.0))
    amp = max(0.0, min(1.0, amp))
    wave_gen.amplitude = amp
    logger.debug(f"Amplitude set to {amp:.3f}")
    ui.send_message("synth:state", {"amplitude": amp})


def on_set_waveform(sid, data=None):
    """Change waveform type."""
    d = data or {}
    waveform = d.get("waveform", "sine")
    valid_waveforms = ["sine", "square", "sawtooth", "triangle"]
    if waveform in valid_waveforms:
        wave_gen.wave_type = waveform
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
        wave_gen.attack = float(attack) / 1000.0
    if release is not None:
        wave_gen.release = float(release) / 1000.0
    if glide is not None:
        wave_gen.glide = float(glide) / 1000.0

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
    wave_gen.volume = volume
    logger.debug(f"Master volume set to {volume}%")
    ui.send_message("synth:state", {"volume": volume})


def on_note_on(sid, data=None):
    """Trigger note via web UI (keyboard emulation)."""
    d = data or {}
    note = int(d.get("note", 60))  # MIDI note number
    velocity = int(d.get("velocity", 100))

    wave_gen.note_on(note, velocity)
    freq = MIDIKeyboard.note_to_frequency(note)
    amp = velocity / 127.0

    logger.info(f"Web UI Note ON: {note} ({freq:.1f}Hz) vel={velocity}")
    ui.send_message("synth:state", {"frequency": freq, "amplitude": amp})


def on_note_off(sid, data=None):
    """Release note via web UI."""
    d = data or {}
    note = d.get("note")
    if note is not None:
        wave_gen.note_off(int(note))
    else:
        wave_gen.all_notes_off()
    logger.info("Web UI Note OFF")
    ui.send_message("synth:state", {"amplitude": 0.0})


def on_set_voices(sid, data=None):
    """Set the number of active polyphonic voices (1–3)."""
    d = data or {}
    voices = max(1, min(PolyWaveGenerator.MAX_VOICES, int(d.get("voices", 1))))
    wave_gen.voices = voices
    logger.info(f"Voices set to {voices}")
    ui.send_message("synth:state", {"voices": voices})


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


def on_set_effects(sid, data=None):
    """Update effects chain parameters."""
    d = data or {}
    changed = {}
    if "cutoff" in d:
        wave_gen.cutoff = float(d["cutoff"])
        changed["cutoff"] = wave_gen.cutoff
    if "resonance" in d:
        wave_gen.resonance = float(d["resonance"]) / 100.0
        changed["resonance"] = wave_gen.resonance * 100
    if "overdrive" in d:
        wave_gen.overdrive = float(d["overdrive"]) / 100.0
        changed["overdrive"] = wave_gen.overdrive * 100
    if "tremolo_depth" in d:
        wave_gen.tremolo_depth = float(d["tremolo_depth"]) / 100.0
        changed["tremolo_depth"] = wave_gen.tremolo_depth * 100
    if "tremolo_rate" in d:
        wave_gen.tremolo_rate = float(d["tremolo_rate"])
        changed["tremolo_rate"] = wave_gen.tremolo_rate
    if "delay_time" in d:
        wave_gen.delay_time = float(d["delay_time"]) / 1000.0
        changed["delay_time"] = wave_gen.delay_time * 1000
    if "delay_feedback" in d:
        wave_gen.delay_feedback = float(d["delay_feedback"]) / 100.0
        changed["delay_feedback"] = wave_gen.delay_feedback * 100
    if "reverb_wet" in d:
        wave_gen.reverb_wet = float(d["reverb_wet"]) / 100.0
        changed["reverb_wet"] = wave_gen.reverb_wet * 100
    logger.debug(f"Effects updated: {changed}")
    if changed:
        ui.send_message("synth:state", {"effects": changed})


# Register UI event handlers
ui.on_connect(on_connect)
ui.on_message("synth:set_frequency", on_set_frequency)
ui.on_message("synth:set_amplitude", on_set_amplitude)
ui.on_message("synth:set_waveform", on_set_waveform)
ui.on_message("synth:set_envelope", on_set_envelope)
ui.on_message("synth:set_effects", on_set_effects)
ui.on_message("synth:set_volume", on_set_volume)
ui.on_message("synth:set_voices", on_set_voices)
ui.on_message("synth:note_on", on_note_on)
ui.on_message("synth:note_off", on_note_off)
ui.on_message("synth:update_cc_mapping", on_update_cc_mapping)
ui.on_message("synth:learn_cc", on_learn_cc)


# --- MIDI event handlers (defined globally for hotplug support) --------------------

# MIDI continuous-controller throttling (same strategy as pitch bend).
# A physical knob or slider can send CC messages at 100-300 Hz; without
# throttling every message would update the synth parameter and cause the
# same crackling artefacts as unthrottled UI slider events.
import time

_CC_THROTTLE_SEC = 0.025  # 25 ms — max 40 updates/sec per parameter
_PITCH_BEND_THROTTLE_SEC = _CC_THROTTLE_SEC  # keep name for compatibility

_last_pitch_bend_time = time.perf_counter()

# Discrete parameters (waveform) map CC values to a small set of choices —
# they are not throttled so every step registers immediately.
_CC_DISCRETE_PARAMS = {"waveform"}

# Per-parameter timestamp dict — populated lazily on first CC message.
_last_cc_time: dict = {}


def on_midi_note_on(note, velocity):
    """Handle MIDI note on."""
    logger.info(f"🎹 MIDI Note ON: note={note} velocity={velocity}")
    wave_gen.note_on(note, velocity)
    freq = MIDIKeyboard.note_to_frequency(note)
    amp = velocity / 127.0
    ui.send_message("synth:state", {"frequency": freq, "amplitude": amp, "source": "midi"})


def on_midi_note_off(note, velocity):
    """Handle MIDI note off."""
    wave_gen.note_off(note)
    logger.debug(f"MIDI Note OFF: {note}")
    ui.send_message("synth:state", {"amplitude": wave_gen.amplitude, "source": "midi"})


def on_midi_pitch_bend(value):
    """Handle pitch bend (±2 semitones). Throttled to max every 25 ms."""
    global _last_pitch_bend_time

    current_time = time.perf_counter()
    if (current_time - _last_pitch_bend_time) < _PITCH_BEND_THROTTLE_SEC:
        return
    _last_pitch_bend_time = current_time

    normalized_bend = (value - 8192) / 8192.0
    bend_semitones = normalized_bend * 2.0
    bend_factor = 2.0 ** (bend_semitones / 12.0)
    wave_gen.set_pitch_bend(bend_factor)
    logger.debug(f"MIDI Pitch bend: {value} (norm={normalized_bend:.2f}) → factor={bend_factor:.4f}")
    ui.send_message("synth:state", {"frequency": wave_gen.frequency, "source": "midi"})


def on_midi_cc(control, value):
    """Handle MIDI CC based on mapping configuration."""
    logger.debug(f"🎹 MIDI CC{control} = {value}")

    now = time.perf_counter()

    # Check if this CC is mapped to any parameter
    for param, mapped_cc in cc_mapping.items():
        if mapped_cc == control:
            # Throttle continuous parameters to avoid audio crackling from
            # high-rate CC streams (knobs, expression pedals, etc.)
            if param not in _CC_DISCRETE_PARAMS:
                last = _last_cc_time.get(param, 0.0)
                if (now - last) < _CC_THROTTLE_SEC:
                    # Drop this message — a later one arriving after the
                    # throttle window will carry the updated value.
                    continue
                _last_cc_time[param] = now

            logger.info(f"MIDI CC{control} ({value}) → {param}")

            if param == "waveform":
                # Map CC value 0-127 to waveform index 0-3
                waveforms = ["sine", "square", "sawtooth", "triangle"]
                idx = min(3, int((value / 127.0) * 4))
                wave_gen.wave_type = waveforms[idx]
                ui.send_message("synth:state", {"waveform": waveforms[idx], "source": "midi"})

            elif param == "attack":
                attack_ms = (value / 127.0) * 500  # 0-500ms
                wave_gen.attack = attack_ms / 1000.0
                ui.send_message("synth:state", {"attack": attack_ms, "envelope": {"attack": attack_ms}, "source": "midi"})

            elif param == "decay":
                decay_ms = (value / 127.0) * 1000
                wave_gen.decay = decay_ms / 1000.0
                ui.send_message("synth:state", {"envelope": {"decay": decay_ms}, "source": "midi"})

            elif param == "sustain":
                sustain_pct = (value / 127.0) * 100
                wave_gen.sustain = sustain_pct / 100.0
                ui.send_message("synth:state", {"envelope": {"sustain": sustain_pct}, "source": "midi"})

            elif param == "release":
                release_ms = (value / 127.0) * 1000  # 0-1000ms
                wave_gen.release = release_ms / 1000.0
                ui.send_message("synth:state", {"release": release_ms, "envelope": {"release": release_ms}, "source": "midi"})

            elif param == "glide":
                glide_ms = (value / 127.0) * 200  # 0-200ms
                wave_gen.glide = glide_ms / 1000.0
                ui.send_message("synth:state", {"glide": glide_ms, "envelope": {"glide": glide_ms}, "source": "midi"})

            elif param == "amplitude":
                amp = value / 127.0
                if wave_gen.has_active_notes:  # Only if notes are playing
                    wave_gen.amplitude = amp
                    ui.send_message("synth:state", {"amplitude": amp, "source": "midi"})

            elif param == "master_volume":
                volume = int((value / 127.0) * 100)
                wave_gen.volume = volume
                ui.send_message("synth:state", {"volume": volume, "source": "midi"})

            elif param == "frequency":
                freq = 100 + (value / 127.0) * 1900
                wave_gen.frequency = freq
                ui.send_message("synth:state", {"frequency": freq, "source": "midi"})

            elif param == "cutoff":
                cutoff = 20.0 + (value / 127.0) * (20000.0 - 20.0)
                wave_gen.cutoff = cutoff
                ui.send_message("synth:state", {"effects": {"cutoff": cutoff}, "source": "midi"})

            elif param == "resonance":
                res_pct = (value / 127.0) * 100
                wave_gen.resonance = res_pct / 100.0
                ui.send_message("synth:state", {"effects": {"resonance": res_pct}, "source": "midi"})

            elif param == "overdrive":
                od_pct = (value / 127.0) * 100
                wave_gen.overdrive = od_pct / 100.0
                ui.send_message("synth:state", {"effects": {"overdrive": od_pct}, "source": "midi"})

            elif param == "tremolo_depth":
                td_pct = (value / 127.0) * 100
                wave_gen.tremolo_depth = td_pct / 100.0
                ui.send_message("synth:state", {"effects": {"tremolo_depth": td_pct}, "source": "midi"})

            elif param == "tremolo_rate":
                rate = 0.1 + (value / 127.0) * 19.9  # 0.1–20 Hz
                wave_gen.tremolo_rate = rate
                ui.send_message("synth:state", {"effects": {"tremolo_rate": rate}, "source": "midi"})

            elif param == "delay_time":
                dt_ms = (value / 127.0) * 1000
                wave_gen.delay_time = dt_ms / 1000.0
                ui.send_message("synth:state", {"effects": {"delay_time": dt_ms}, "source": "midi"})

            elif param == "delay_feedback":
                fb_pct = (value / 127.0) * 95  # max 95%
                wave_gen.delay_feedback = fb_pct / 100.0
                ui.send_message("synth:state", {"effects": {"delay_feedback": fb_pct}, "source": "midi"})

            elif param == "reverb_wet":
                rv_pct = (value / 127.0) * 100
                wave_gen.reverb_wet = rv_pct / 100.0
                ui.send_message("synth:state", {"effects": {"reverb_wet": rv_pct}, "source": "midi"})

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
