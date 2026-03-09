# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

"""MIDI Keyboard peripheral for Linux/ALSA.

Self-contained module providing MIDIKeyboard input support for Arduino UNO Q
and other Linux boards with ALSA support. Uses direct ALSA device access
(/dev/snd/midiC*D*) without external dependencies.
"""

import logging
import threading
import time
from typing import Callable, Optional, Dict

from arduino.app_utils import Logger

logger = Logger("MIDIKeyboard", logging.DEBUG)


# =============================================================================
# MIDI Profiles
# =============================================================================


class MIDIProfile:
    """Base class for MIDI controller profiles."""

    def __init__(self):
        self.name = "Generic"
        self.note_map: Dict[int, str] = {}
        self.cc_map: Dict[int, str] = {}
        self.has_aftertouch = False
        self.has_pitchbend = False


class AkaiMPKMiniPlusProfile(MIDIProfile):
    def __init__(self):
        super().__init__()
        self.name = "Akai MPK Mini Plus"
        self.note_map = {
            36: "pad_1",
            37: "pad_2",
            38: "pad_3",
            39: "pad_4",
            40: "pad_5",
            41: "pad_6",
            42: "pad_7",
            43: "pad_8",
        }
        self.cc_map = {
            70: "knob_1",
            71: "knob_2",
            72: "knob_3",
            73: "knob_4",
            74: "knob_5",
            75: "knob_6",
            76: "knob_7",
            77: "knob_8",
            1: "modwheel",
        }
        self.has_pitchbend = True


class AkaiMPCMiniProfile(MIDIProfile):
    def __init__(self):
        super().__init__()
        self.name = "Akai MPC Mini"
        self.note_map = {
            36: "pad_1",
            37: "pad_2",
            38: "pad_3",
            39: "pad_4",
            40: "pad_5",
            41: "pad_6",
            42: "pad_7",
            43: "pad_8",
            44: "pad_9",
            45: "pad_10",
            46: "pad_11",
            47: "pad_12",
            48: "pad_13",
            49: "pad_14",
            50: "pad_15",
            51: "pad_16",
        }
        for i in range(16):
            self.note_map[52 + i] = f"pad_b_{i + 1}"
        self.cc_map = {
            70: "knob_1",
            71: "knob_2",
            72: "knob_3",
            73: "knob_4",
            74: "knob_5",
            75: "knob_6",
            76: "knob_7",
            77: "knob_8",
        }


class NIMaschineMikroProfile(MIDIProfile):
    def __init__(self):
        super().__init__()
        self.name = "NI Maschine Mikro MK3"
        self.note_map = {
            36: "pad_1",
            37: "pad_2",
            38: "pad_3",
            39: "pad_4",
            40: "pad_5",
            41: "pad_6",
            42: "pad_7",
            43: "pad_8",
            44: "pad_9",
            45: "pad_10",
            46: "pad_11",
            47: "pad_12",
            48: "pad_13",
            49: "pad_14",
            50: "pad_15",
            51: "pad_16",
        }
        self.cc_map = {22: "encoder", 1: "touch_strip"}
        self.has_aftertouch = True
        self.has_pitchbend = True


class LaunchpadMiniProfile(MIDIProfile):
    def __init__(self):
        super().__init__()
        self.name = "Novation Launchpad Mini"
        self.note_map = {}
        for row in range(8):
            for col in range(8):
                self.note_map[row * 16 + col] = f"pad_{row}_{col}"
        for i in range(8):
            self.cc_map[104 + i] = f"side_button_{i}"
            self.note_map[104 + i] = f"top_button_{i}"


class GeneralMIDIDrumMapProfile(MIDIProfile):
    def __init__(self):
        super().__init__()
        self.name = "General MIDI Drum Map"
        self.note_map = {
            35: "kick_acoustic",
            36: "kick",
            37: "side_stick",
            38: "snare_acoustic",
            39: "clap",
            40: "snare_electric",
            41: "tom_low_floor",
            42: "hihat_closed",
            43: "tom_low",
            44: "hihat_pedal",
            45: "tom_mid",
            46: "hihat_open",
            47: "tom_mid_low",
            48: "tom_mid_high",
            49: "crash_1",
            50: "tom_high",
            51: "ride_1",
            52: "chinese",
            53: "ride_bell",
            54: "tambourine",
            55: "splash",
            56: "cowbell",
            57: "crash_2",
            58: "vibraslap",
            59: "ride_2",
        }


_PROFILES = {
    "generic": MIDIProfile,
    "akai_mpk_mini_plus": AkaiMPKMiniPlusProfile,
    "akai_mpc_mini": AkaiMPCMiniProfile,
    "ni_maschine_mikro": NIMaschineMikroProfile,
    "launchpad_mini": LaunchpadMiniProfile,
    "gm_drums": GeneralMIDIDrumMapProfile,
}


def _load_profile(profile_name: str) -> MIDIProfile:
    if profile_name not in _PROFILES:
        raise ValueError(f"Unknown profile '{profile_name}'. Available: {list(_PROFILES.keys())}")
    return _PROFILES[profile_name]()


def _list_available_profiles() -> list:
    return list(_PROFILES.keys())


# =============================================================================
# MIDI Message container
# =============================================================================


class MidiMessage:
    """Minimal MIDI message container."""

    def __init__(self, msg_type: str, **kwargs):
        self.type = msg_type
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        attrs = ", ".join(f"{k}={v}" for k, v in self.__dict__.items() if k != "type")
        return f"<MidiMessage {self.type} {attrs}>"


# =============================================================================
# MIDIKeyboard
# =============================================================================


class MIDIKeyboardException(Exception):
    pass


class MIDIKeyboard:
    """MIDI keyboard/controller input for Linux/ALSA.

    Handles MIDI input from USB MIDI devices including keyboards, drum pads,
    and control surfaces. Provides callbacks for note events, control changes,
    and pitch bend.

    Uses direct ALSA device access (/dev/snd/midiC*D*) without external
    dependencies. Designed for Arduino UNO Q and other Linux boards with ALSA.
    """

    USB_MIDI_1 = "USB_MIDI_1"
    USB_MIDI_2 = "USB_MIDI_2"

    def __init__(
        self,
        device: Optional[str] = USB_MIDI_1,
        channel: Optional[int] = None,
        profile: Optional[str] = None,
    ):
        logger.info("Init MIDIKeyboard with device=%s, channel=%s, profile=%s", device, channel, profile)

        self.channel = channel
        self.profile_name = profile
        self.friendly_name = None
        self._port = None
        self._is_running = threading.Event()
        self._listener_thread = None

        self._note_on_callbacks: Dict[int, Callable] = {}
        self._note_off_callbacks: Dict[int, Callable] = {}
        self._cc_callbacks: Dict[int, Callable] = {}
        self._pitchbend_callback: Optional[Callable] = None
        self._aftertouch_callback: Optional[Callable] = None

        self._global_note_on_callback: Optional[Callable] = None
        self._global_note_off_callback: Optional[Callable] = None
        self._global_cc_callback: Optional[Callable] = None

        self._semantic_pad_callbacks: Dict[str, Callable] = {}
        self._semantic_knob_callbacks: Dict[str, Callable] = {}

        self._profile = None
        if profile:
            self._profile = _load_profile(profile)
            logger.info(f"Loaded profile: {self._profile.name}")

        self.device_name = self._resolve_device(device)
        logger.info(f"Using MIDI device: {self.device_name}")

    def _open_alsa_seq(self, device_name: str):
        import select

        class AlsaSeqPort:
            def __init__(self, device_name):
                if device_name.startswith("hw:"):
                    parts = device_name[3:].split(",")
                    card = int(parts[0])
                    dev = int(parts[1]) if len(parts) > 1 else 0
                    self.device_path = f"/dev/snd/midiC{card}D{dev}"
                else:
                    self.device_path = device_name

                self.fd = open(self.device_path, "rb", buffering=0)
                logger.info(f"Opened raw ALSA device: {self.device_path}")

            def receive(self, block=True):
                if not block:
                    rlist, _, _ = select.select([self.fd], [], [], 0)
                    if not rlist:
                        return None

                try:
                    status_byte = self.fd.read(1)
                    if not status_byte:
                        return None

                    status = status_byte[0]

                    if (status & 0xF0) == 0x90:
                        data = self.fd.read(2)
                        note, velocity = data[0], data[1]
                        return MidiMessage("note_on", note=note, velocity=velocity, channel=status & 0x0F)

                    elif (status & 0xF0) == 0x80:
                        data = self.fd.read(2)
                        note, velocity = data[0], data[1]
                        return MidiMessage("note_off", note=note, velocity=velocity, channel=status & 0x0F)

                    elif (status & 0xF0) == 0xB0:
                        data = self.fd.read(2)
                        control, value = data[0], data[1]
                        return MidiMessage("control_change", control=control, value=value, channel=status & 0x0F)

                    elif (status & 0xF0) == 0xE0:
                        data = self.fd.read(2)
                        lsb, msb = data[0], data[1]
                        pitch = (msb << 7) | lsb
                        return MidiMessage("pitchwheel", pitch=pitch, channel=status & 0x0F)

                    elif (status & 0xF0) == 0xD0:
                        data = self.fd.read(1)
                        value = data[0]
                        return MidiMessage("aftertouch", value=value, channel=status & 0x0F)

                    elif (status & 0xF0) == 0xA0:
                        data = self.fd.read(2)
                        note, value = data[0], data[1]
                        return MidiMessage("polytouch", note=note, value=value, channel=status & 0x0F)

                    else:
                        logger.debug(f"Skipping MIDI status byte: 0x{status:02x}")
                        return None

                except (OSError, IOError) as e:
                    logger.debug(f"ALSA device read error: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Error parsing ALSA MIDI message: {e}")
                    return None

            def close(self):
                if self.fd:
                    self.fd.close()
                    self.fd = None

        return AlsaSeqPort(device_name)

    def _resolve_device(self, device: Optional[str]) -> str:
        available = self.list_usb_devices()

        if not available:
            raise MIDIKeyboardException("No MIDI input devices found.")

        if device is None or device == self.USB_MIDI_1:
            self.friendly_name = available[0]["friendly_name"]
            return available[0]["hw_name"]

        if device == self.USB_MIDI_2:
            if len(available) < 2:
                raise MIDIKeyboardException(f"USB_MIDI_2 requested but only {len(available)} device(s) found.")
            self.friendly_name = available[1]["friendly_name"]
            return available[1]["hw_name"]

        for dev in available:
            if device == dev["hw_name"]:
                self.friendly_name = dev["friendly_name"]
                return dev["hw_name"]

        for dev in available:
            hw_match = device.lower() in dev["hw_name"].lower()
            friendly_match = dev["friendly_name"] and device.lower() in dev["friendly_name"].lower()
            if hw_match or friendly_match:
                logger.info(f"Matched device '{device}' to '{dev['friendly_name']}' ({dev['hw_name']})")
                self.friendly_name = dev["friendly_name"]
                return dev["hw_name"]

        available_names = [f"{d['friendly_name']} ({d['hw_name']})" for d in available]
        raise MIDIKeyboardException(f"MIDI device '{device}' not found. Available: {available_names}")

    @staticmethod
    def _get_alsa_card_name(card_num: int) -> str:
        import os
        import subprocess

        try:
            card_id_path = f"/sys/class/sound/card{card_num}/id"
            if os.path.exists(card_id_path):
                with open(card_id_path, "r") as f:
                    card_id = f.read().strip()

                try:
                    cmd = f"readlink /sys/class/sound/card{card_num} | xargs -I{{}} cat /sys/class/sound/{{}}/device/../product 2>/dev/null"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
                    if result.returncode == 0 and result.stdout.strip():
                        return result.stdout.strip()
                except Exception as e:
                    logger.debug(f"Could not read USB product name: {e}")

                return card_id

            cards_path = "/proc/asound/cards"
            if os.path.exists(cards_path) and os.access(cards_path, os.R_OK):
                with open(cards_path, "r") as f:
                    for line in f:
                        stripped = line.lstrip()
                        if stripped.startswith(f"{card_num} "):
                            if " - " in line:
                                return line.split(" - ", 1)[1].strip()
                            elif "[" in line and "]" in line:
                                return line.split("[")[1].split("]")[0].strip()

            return f"ALSA Card {card_num}"

        except Exception as e:
            logger.debug(f"Error reading ALSA card name for card {card_num}: {e}")
            return f"ALSA Card {card_num}"

    @staticmethod
    def list_usb_devices() -> list:
        import glob
        import re

        devices = []
        midi_devices = glob.glob("/dev/snd/midiC*D*")

        if midi_devices:
            for dev in sorted(midi_devices):
                match = re.search(r"midiC(\d+)D(\d+)", dev)
                if match:
                    card, device = match.groups()
                    hw_name = f"hw:{card},{device}"
                    friendly_name = MIDIKeyboard._get_alsa_card_name(int(card))
                    devices.append({"hw_name": hw_name, "friendly_name": friendly_name})

            logger.info(f"Available MIDI inputs (ALSA): {[d['friendly_name'] for d in devices]}")
            return devices
        else:
            logger.warning("No MIDI devices found in /dev/snd")
            return []

    def start(self):
        if self._is_running.is_set():
            logger.warning("MIDIKeyboard is already running")
            return

        try:
            self._port = self._open_alsa_seq(self.device_name)
            logger.info(f"Opened ALSA sequencer port: {self.device_name}")
        except Exception as e:
            raise MIDIKeyboardException(f"Failed to open MIDI device: {e}")

        self._is_running.set()
        self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True, name="MIDIKeyboard-Listener")
        self._listener_thread.start()
        logger.info("MIDIKeyboard started")

    def stop(self):
        if not self._is_running.is_set():
            logger.warning("MIDIKeyboard is not running")
            return

        logger.info("Stopping MIDIKeyboard...")
        self._is_running.clear()

        if self._listener_thread:
            self._listener_thread.join(timeout=2)
            if self._listener_thread.is_alive():
                logger.warning("Listener thread did not terminate in time")
            self._listener_thread = None

        if self._port:
            try:
                self._port.close()
                logger.info("MIDI port closed")
            except Exception as e:
                logger.warning(f"Error closing MIDI port: {e}")
            self._port = None

        logger.info("MIDIKeyboard stopped")

    def is_connected(self) -> bool:
        return self._is_running.is_set() and self._listener_thread and self._listener_thread.is_alive()

    def _listen_loop(self):
        logger.debug("MIDI listener loop started")

        while self._is_running.is_set():
            try:
                msg = self._port.receive(block=False)

                if msg is None:
                    time.sleep(0.001)
                    continue

                if self.channel is not None and hasattr(msg, "channel"):
                    if msg.channel + 1 != self.channel:
                        continue

                self._process_message(msg)

            except (OSError, IOError) as e:
                logger.error(f"MIDI device disconnected: {e}")
                self._is_running.clear()
                break
            except Exception as e:
                logger.error(f"Error in MIDI listener loop: {e}")
                time.sleep(0.1)

        logger.debug("MIDI listener loop terminated")

    def _process_message(self, msg):
        try:
            if msg.type == "note_on":
                if msg.velocity == 0:
                    self._handle_note_off(msg.note, 0)
                else:
                    self._handle_note_on(msg.note, msg.velocity)
            elif msg.type == "note_off":
                self._handle_note_off(msg.note, msg.velocity)
            elif msg.type == "control_change":
                self._handle_cc(msg.control, msg.value)
            elif msg.type == "pitchwheel":
                self._handle_pitchbend(msg.pitch)
            elif msg.type == "aftertouch":
                self._handle_aftertouch(msg.value)
            elif msg.type == "polytouch":
                logger.debug(f"Poly aftertouch: note={msg.note}, value={msg.value}")
        except Exception as e:
            logger.error(f"Error processing MIDI message {msg}: {e}")

    def _handle_note_on(self, note: int, velocity: int):
        logger.debug(f"Note ON: {note}, velocity: {velocity}")

        if note in self._note_on_callbacks:
            try:
                self._note_on_callbacks[note](velocity)
            except Exception as e:
                logger.error(f"Error in note_on callback for note {note}: {e}")

        if self._global_note_on_callback:
            try:
                self._global_note_on_callback(note, velocity)
            except Exception as e:
                logger.error(f"Error in global note_on callback: {e}")

        if self._profile and note in self._profile.note_map:
            semantic_name = self._profile.note_map[note]
            if semantic_name in self._semantic_pad_callbacks:
                try:
                    self._semantic_pad_callbacks[semantic_name](velocity)
                except Exception as e:
                    logger.error(f"Error in semantic pad callback for {semantic_name}: {e}")

    def _handle_note_off(self, note: int, velocity: int):
        logger.debug(f"Note OFF: {note}, velocity: {velocity}")

        if note in self._note_off_callbacks:
            try:
                self._note_off_callbacks[note](velocity)
            except Exception as e:
                logger.error(f"Error in note_off callback for note {note}: {e}")

        if self._global_note_off_callback:
            try:
                self._global_note_off_callback(note, velocity)
            except Exception as e:
                logger.error(f"Error in global note_off callback: {e}")

    def _handle_cc(self, control: int, value: int):
        logger.debug(f"CC: {control}, value: {value}")

        if control in self._cc_callbacks:
            try:
                self._cc_callbacks[control](value)
            except Exception as e:
                logger.error(f"Error in CC callback for control {control}: {e}")

        if self._global_cc_callback:
            try:
                self._global_cc_callback(control, value)
            except Exception as e:
                logger.error(f"Error in global CC callback: {e}")

        if self._profile and control in self._profile.cc_map:
            semantic_name = self._profile.cc_map[control]
            if semantic_name in self._semantic_knob_callbacks:
                try:
                    self._semantic_knob_callbacks[semantic_name](value)
                except Exception as e:
                    logger.error(f"Error in semantic knob callback for {semantic_name}: {e}")

    def _handle_pitchbend(self, value: int):
        logger.debug(f"Pitch bend: {value}")
        if self._pitchbend_callback:
            try:
                self._pitchbend_callback(value)
            except Exception as e:
                logger.error(f"Error in pitchbend callback: {e}")

    def _handle_aftertouch(self, value: int):
        logger.debug(f"Aftertouch: {value}")
        if self._aftertouch_callback:
            try:
                self._aftertouch_callback(value)
            except Exception as e:
                logger.error(f"Error in aftertouch callback: {e}")

    def on_note_on(self, callback: Callable, note: Optional[int] = None):
        if note is None:
            self._global_note_on_callback = callback
        else:
            self._note_on_callbacks[note] = callback

    def on_note_off(self, callback: Callable, note: Optional[int] = None):
        if note is None:
            self._global_note_off_callback = callback
        else:
            self._note_off_callbacks[note] = callback

    def on_control_change(self, callback: Callable, control: Optional[int] = None):
        if control is None:
            self._global_cc_callback = callback
        else:
            self._cc_callbacks[control] = callback

    def on_pitch_bend(self, callback: Callable):
        self._pitchbend_callback = callback

    def on_aftertouch(self, callback: Callable):
        self._aftertouch_callback = callback

    def on_pad(self, pad_name: str, callback: Callable):
        if not self._profile:
            raise MIDIKeyboardException("on_pad() requires a profile. Initialize with profile parameter.")
        self._semantic_pad_callbacks[pad_name] = callback

    def on_knob(self, knob_name: str, callback: Callable):
        if not self._profile:
            raise MIDIKeyboardException("on_knob() requires a profile. Initialize with profile parameter.")
        self._semantic_knob_callbacks[knob_name] = callback

    @staticmethod
    def note_to_frequency(note: int) -> float:
        return 440.0 * (2.0 ** ((note - 69) / 12.0))

    @staticmethod
    def frequency_to_note(frequency: float) -> int:
        import math

        return int(round(69 + 12 * math.log2(frequency / 440.0)))

    def get_profile_info(self) -> Optional[dict]:
        if not self._profile:
            return None
        return {
            "name": self._profile.name,
            "note_map": self._profile.note_map,
            "cc_map": self._profile.cc_map,
            "has_aftertouch": self._profile.has_aftertouch,
            "has_pitchbend": self._profile.has_pitchbend,
        }

    @staticmethod
    def list_profiles() -> list:
        return _list_available_profiles()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
