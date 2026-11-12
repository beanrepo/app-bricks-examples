# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Logger
from midi_keyboard import MIDIKeyboard, note_to_frequency
from poly_wave_generator import PolyWaveGenerator

logger = Logger("synth-emulator")

WAVEFORMS = ("sine", "square", "sawtooth", "triangle")

# Polyphonic wave generator (1-3 voices sharing one speaker stream). With no
# speaker given it opens the first available one: USB first, then the 3.5 mm
# jack of the UNO Media Carrier.
wave_gen = PolyWaveGenerator(wave_type="sine", attack=0.01, release=0.1, glide=0.0)

# MIDI input is optional: the first USB MIDI device is used when plugged in.
midi = MIDIKeyboard()


# --- Synth parameters -----------------------------------------------------------
# One table describes how every controllable parameter maps between the MIDI CC
# range (0-127), the generator attribute (seconds, Hz, 0-1) and the web UI units
# (ms, %, Hz).


@dataclass(frozen=True)
class SynthParam:
    attr: str  # PolyWaveGenerator attribute
    lo: float  # generator value for CC 0
    hi: float  # generator value for CC 127
    ui_group: str | None  # "envelope", "effects" or None for a top-level state key
    ui_scale: float = 1.0  # generator unit -> UI unit (1000 = seconds to ms, 100 = ratio to %)
    ui_key: str | None = None  # state key when it differs from the parameter name

    def set_from_cc(self, value: int) -> float:
        """Applies a CC value and returns the new value in UI units."""
        setattr(wave_gen, self.attr, self.lo + (self.hi - self.lo) * value / 127.0)
        return self.ui_value()

    def set_from_ui(self, value: Any) -> float:
        """Applies a value in UI units and returns it as clamped by the generator."""
        setattr(wave_gen, self.attr, float(value) / self.ui_scale)
        return self.ui_value()

    def ui_value(self) -> float:
        return getattr(wave_gen, self.attr) * self.ui_scale


PARAMS: dict[str, SynthParam] = {
    "attack": SynthParam("attack", 0.0, 0.5, "envelope", 1000),
    "decay": SynthParam("decay", 0.0, 1.0, "envelope", 1000),
    "sustain": SynthParam("sustain", 0.0, 1.0, "envelope", 100),
    "release": SynthParam("release", 0.0, 1.0, "envelope", 1000),
    "glide": SynthParam("glide", 0.0, 0.2, "envelope", 1000),
    "cutoff": SynthParam("cutoff", 20.0, 20000.0, "effects"),
    "resonance": SynthParam("resonance", 0.0, 1.0, "effects", 100),
    "overdrive": SynthParam("overdrive", 0.0, 1.0, "effects", 100),
    "tremolo_depth": SynthParam("tremolo_depth", 0.0, 1.0, "effects", 100),
    "tremolo_rate": SynthParam("tremolo_rate", 0.1, 20.0, "effects"),
    "delay_time": SynthParam("delay_time", 0.0, 1.0, "effects", 1000),
    "delay_feedback": SynthParam("delay_feedback", 0.0, 0.95, "effects", 100),
    "reverb_wet": SynthParam("reverb_wet", 0.0, 1.0, "effects", 100),
    "frequency": SynthParam("frequency", 100.0, 2000.0, None),
    "amplitude": SynthParam("amplitude", 0.0, 1.0, None),
    "master_volume": SynthParam("volume", 0.0, 100.0, None, ui_key="volume"),
}
ENVELOPE_PARAMS = [name for name, p in PARAMS.items() if p.ui_group == "envelope"]
EFFECT_PARAMS = [name for name, p in PARAMS.items() if p.ui_group == "effects"]

# MIDI CC mapping: parameter -> CC number, editable from the UI ("waveform"
# selects the wave type from the CC value). Defaults follow common controllers.
cc_mapping: dict[str, int | None] = {"waveform": None, **{name: None for name in PARAMS}}
cc_mapping.update({"attack": 1, "release": 2, "glide": 3, "amplitude": 7, "master_volume": 11})


def ui_payload(name: str, ui_value: Any) -> dict[str, Any]:
    """State message fragment for one parameter, nested by UI group."""
    param = PARAMS.get(name)
    key = param.ui_key or name if param else name
    if param and param.ui_group:
        return {param.ui_group: {key: ui_value}}
    return {key: ui_value}


def full_state() -> dict[str, Any]:
    state = wave_gen.state
    return {
        "frequency": state["frequency"],
        "amplitude": state["amplitude"],
        "waveform": state["wave_type"],
        "volume": state["volume"],
        "voices": state["voices"],
        "envelope": {name: PARAMS[name].ui_value() for name in ENVELOPE_PARAMS},
        "effects": {name: PARAMS[name].ui_value() for name in EFFECT_PARAMS},
    }


def midi_status() -> dict[str, Any]:
    return {"available": midi.status == "connected", "device_name": midi.name}


# --- Web UI ---------------------------------------------------------------------
ui = WebUI()

# MIDI callbacks run on the MIDI listener thread. Serializing and sending a
# socket message there holds the GIL long enough to delay the audio thread, so
# UI messages are queued and sent by a dedicated thread instead.
_ui_queue: queue.SimpleQueue[tuple[str, dict[str, Any]]] = queue.SimpleQueue()


def _ui_dispatch_loop() -> None:
    while True:
        event, data = _ui_queue.get()
        ui.send_message(event, data)


threading.Thread(target=_ui_dispatch_loop, daemon=True, name="UI-Dispatch").start()


def on_connect(sid: str, data: Any = None) -> None:
    """Sends the whole synth state to a newly connected client."""
    ui.send_message("synth:state", full_state(), room=sid)
    ui.send_message("synth:cc_mapping", cc_mapping, room=sid)
    ui.send_message("synth:midi_status", midi_status(), room=sid)


def on_set_frequency(sid: str, data: Any = None) -> None:
    freq = PARAMS["frequency"].set_from_ui((data or {}).get("frequency", 440.0))
    ui.send_message("synth:state", {"frequency": freq})


def on_set_amplitude(sid: str, data: Any = None) -> None:
    amp = PARAMS["amplitude"].set_from_ui((data or {}).get("amplitude", 0.0))
    ui.send_message("synth:state", {"amplitude": amp})


def on_set_volume(sid: str, data: Any = None) -> None:
    volume = PARAMS["master_volume"].set_from_ui((data or {}).get("volume", 100))
    ui.send_message("synth:state", {"volume": volume})


def on_set_waveform(sid: str, data: Any = None) -> None:
    waveform = (data or {}).get("waveform", "sine")
    if waveform not in WAVEFORMS:
        logger.warning(f"Invalid waveform: {waveform}")
        return
    wave_gen.wave_type = waveform
    logger.info(f"Waveform changed to: {waveform}")
    ui.send_message("synth:state", {"waveform": waveform})


def _set_group_from_ui(group: str, names: list[str], data: Any) -> None:
    d = data or {}
    changed = {name: PARAMS[name].set_from_ui(d[name]) for name in names if name in d}
    if changed:
        logger.debug(f"{group} updated: {changed}")
        ui.send_message("synth:state", {group: changed})


def on_set_envelope(sid: str, data: Any = None) -> None:
    """Updates the envelope (attack, decay, sustain, release, glide) from UI units."""
    _set_group_from_ui("envelope", ENVELOPE_PARAMS, data)


def on_set_effects(sid: str, data: Any = None) -> None:
    """Updates the effects chain from UI units."""
    _set_group_from_ui("effects", EFFECT_PARAMS, data)


def on_set_voices(sid: str, data: Any = None) -> None:
    voices = max(1, min(PolyWaveGenerator.MAX_VOICES, int((data or {}).get("voices", 1))))
    wave_gen.voices = voices
    ui.send_message("synth:state", {"voices": voices})


def on_note_on(sid: str, data: Any = None) -> None:
    """Plays a note from the virtual keyboard."""
    d = data or {}
    note = int(d.get("note", 60))
    velocity = int(d.get("velocity", 100))
    wave_gen.note_on(note, velocity)
    ui.send_message("synth:state", {"frequency": note_to_frequency(note), "amplitude": velocity / 127.0})


def on_note_off(sid: str, data: Any = None) -> None:
    note = (data or {}).get("note")
    if note is None:
        wave_gen.all_notes_off()
    else:
        wave_gen.note_off(int(note))
    ui.send_message("synth:state", {"amplitude": 0.0})


def on_update_cc_mapping(sid: str, data: Any = None) -> None:
    d = data or {}
    param = d.get("param")
    if param not in cc_mapping:
        logger.warning(f"Invalid parameter for CC mapping: {param}")
        return
    cc = d.get("cc")
    cc_mapping[param] = int(cc) if cc is not None else None
    logger.info(f"CC mapping updated: {param} -> CC{cc_mapping[param]}")
    ui.send_message("synth:cc_mapping", cc_mapping)


def on_learn_cc(sid: str, data: Any = None) -> None:
    """The UI captures the next CC it receives and assigns it to the parameter."""
    param = (data or {}).get("param")
    logger.info(f"CC learn mode activated for: {param}")
    ui.send_message("synth:cc_learn", {"param": param})


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


# --- MIDI -----------------------------------------------------------------------
# Knobs and wheels send messages at a few hundred Hz: each is applied at most
# every 25 ms so that parameter changes stay smooth and the audio thread is not
# starved. The generator smooths the values in between.
_THROTTLE_SEC = 0.025
_last_cc_time: dict[int, float] = {}
_last_bend_time = 0.0
PITCH_BEND_SEMITONES = 2.0


def on_midi_note_on(note: int, velocity: int) -> None:
    wave_gen.note_on(note, velocity)
    _ui_queue.put(("synth:state", {"frequency": note_to_frequency(note), "amplitude": velocity / 127.0, "source": "midi"}))


def on_midi_note_off(note: int, velocity: int) -> None:
    wave_gen.note_off(note)
    _ui_queue.put(("synth:state", {"amplitude": wave_gen.amplitude, "source": "midi"}))


def on_midi_pitch_bend(value: int) -> None:
    """Bends all playing notes by up to +/- PITCH_BEND_SEMITONES."""
    global _last_bend_time
    now = time.perf_counter()
    if now - _last_bend_time < _THROTTLE_SEC:
        return
    _last_bend_time = now
    semitones = value / 8192.0 * PITCH_BEND_SEMITONES
    wave_gen.set_pitch_bend(2.0 ** (semitones / 12.0))
    _ui_queue.put(("synth:state", {"frequency": wave_gen.frequency, "source": "midi"}))


def on_midi_cc(control: int, value: int) -> None:
    """Applies a CC to the parameters mapped to it and reports it to the UI."""
    now = time.perf_counter()
    if now - _last_cc_time.get(control, 0.0) < _THROTTLE_SEC:
        return
    _last_cc_time[control] = now

    for name, mapped in cc_mapping.items():
        if mapped != control:
            continue
        if name == "waveform":
            waveform = WAVEFORMS[min(len(WAVEFORMS) - 1, value * len(WAVEFORMS) // 128)]
            wave_gen.wave_type = waveform
            _ui_queue.put(("synth:state", {"waveform": waveform, "source": "midi"}))
        elif name == "amplitude" and not wave_gen.has_active_notes:
            continue  # a volume knob must not start a sound by itself
        else:
            ui_value = PARAMS[name].set_from_cc(value)
            _ui_queue.put(("synth:state", {**ui_payload(name, ui_value), "source": "midi"}))

    # Raw CC for the activity monitor and the learn mode
    _ui_queue.put(("synth:midi_cc", {"control": control, "value": value}))


def on_midi_status(status: str, info: dict[str, Any]) -> None:
    logger.info(f"MIDI device {status}: {info.get('name')}")
    if status != "connected":
        wave_gen.all_notes_off()  # no note off will arrive for keys held during the unplug
    _ui_queue.put(("synth:midi_status", midi_status()))


midi.on_note_on(on_midi_note_on)
midi.on_note_off(on_midi_note_off)
midi.on_pitch_bend(on_midi_pitch_bend)
midi.on_control_change(on_midi_cc)
midi.on_status_changed(on_midi_status)
midi.start()

wave_gen.speaker.on_status_changed(lambda status, info: logger.info(f"Audio output {status}: {info}"))

App.run()
