# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""USB MIDI input for Linux boards, built on the raw ALSA MIDI devices.

Reads ``/dev/snd/midiC*D*`` directly, so it needs no extra Python package and
works on the Arduino UNO Q and VENTUNO Q as they ship. The peripheral follows
the conventions of the ``arduino.app_peripherals`` classes (``start``/``stop``,
``status``, ``on_status_changed``, automatic reconnection) so that it can be
moved into the library unchanged.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import select
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from arduino.app_utils import AppError, Logger, peripheral

__all__ = [
    "MIDIDevice",
    "MIDIKeyboard",
    "MIDIKeyboardError",
    "MIDIKeyboardOpenError",
    "frequency_to_note",
    "list_midi_inputs",
    "note_to_frequency",
]

logger = Logger("MIDIKeyboard")

_MIDI_DEVICE_RE = re.compile(r"midiC(\d+)D(\d+)$")
_USB_PATH_RE = re.compile(r"[\\/]usb\d+[\\/]")


class MIDIKeyboardError(AppError):
    """Base exception for MIDI keyboard errors."""


class MIDIKeyboardOpenError(MIDIKeyboardError):
    """Raised when the MIDI input device cannot be opened."""


def note_to_frequency(note: int) -> float:
    """Frequency in Hz of a MIDI note number (A4 = 69 = 440 Hz)."""
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def frequency_to_note(frequency: float) -> int:
    """Nearest MIDI note number for a frequency in Hz."""
    return round(69 + 12 * math.log2(frequency / 440.0))


@dataclass(frozen=True)
class MIDIDevice:
    """A MIDI input port exposed by the kernel.

    Attributes:
        hw_name (str): ALSA name, e.g. ``"hw:1,0"``.
        path (str): Raw MIDI device file, e.g. ``"/dev/snd/midiC1D0"``.
        name (str): Human readable name (USB product name when available).
        is_usb (bool): True when the port belongs to a USB device.
    """

    hw_name: str
    path: str
    name: str
    is_usb: bool


def _card_name(card: int) -> str:
    """Readable name of an ALSA card: USB product name, then the ALSA long name, then its id."""
    sysfs = f"/sys/class/sound/card{card}"
    for rel in ("device/product", "id"):
        try:
            with open(os.path.join(sysfs, rel), encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                if rel == "id":
                    return _proc_asound_long_name(card) or value
                return value
        except OSError:
            continue
    return f"ALSA card {card}"


def _proc_asound_long_name(card: int) -> str | None:
    """Long card name from ``/proc/asound/cards`` (the text after " - ")."""
    try:
        with open("/proc/asound/cards", encoding="utf-8") as f:
            for line in f:
                stripped = line.lstrip()
                if stripped.startswith(f"{card} ") and " - " in stripped:
                    return stripped.split(" - ", 1)[1].strip()
    except OSError:
        pass
    return None


def _card_is_usb(card: int) -> bool:
    try:
        return _USB_PATH_RE.search(os.path.realpath(f"/sys/class/sound/card{card}")) is not None
    except OSError:
        return False


def list_midi_inputs() -> list[MIDIDevice]:
    """Lists the MIDI input ports, USB devices first, in card/device order."""
    devices: list[tuple[int, int, MIDIDevice]] = []
    try:
        entries = os.listdir("/dev/snd")
    except OSError:
        return []
    for entry in entries:
        m = _MIDI_DEVICE_RE.search(entry)
        if not m:
            continue
        card, dev = int(m.group(1)), int(m.group(2))
        devices.append(
            (
                card,
                dev,
                MIDIDevice(hw_name=f"hw:{card},{dev}", path=f"/dev/snd/{entry}", name=_card_name(card), is_usb=_card_is_usb(card)),
            )
        )
    devices.sort(key=lambda item: (not item[2].is_usb, item[0], item[1]))
    return [d for _, _, d in devices]


@peripheral
class MIDIKeyboard:
    """MIDI input from a USB keyboard or controller.

    Delivers note, control change, pitch bend, aftertouch and program change
    events to callbacks. The device is optional and hot-pluggable: with
    ``auto_reconnect`` (the default) ``start()`` never fails when no device is
    present, the keyboard is picked up as soon as it is plugged in, and it is
    reopened after an unplug. ``status`` and ``on_status_changed`` report the
    transitions.

    Callbacks run on the listener thread, right after the bytes are read:
    keep them short and hand any slow work (UI updates, logging at scale) to
    another thread.

    Args:
        device (str | int | None): Which port to open:
            - ``None``: the first USB MIDI input (then any other MIDI input).
            - ``"usb:N"`` or an int ``N``: the N-th port, 1-based, USB first.
            - ``"hw:C,D"``: an ALSA port name.
            - any other string: a case-insensitive substring of the device name.
        channel (int | None): MIDI channel to listen to, 1-16. ``None`` listens to all channels.
        auto_reconnect (bool): Wait for the device and reopen it after an unplug. Default: True.

    Example:
        ```python
        midi = MIDIKeyboard()
        midi.on_note_on(lambda note, velocity: print("on", note, velocity))
        midi.on_note_off(lambda note, velocity: print("off", note))
        midi.start()
        ```
    """

    USB_MIDI_1 = "usb:1"
    USB_MIDI_2 = "usb:2"

    reconnect_delay = 1.0  # seconds between device scans while disconnected

    def __init__(self, device: str | int | None = None, channel: int | None = None, auto_reconnect: bool = True) -> None:
        if channel is not None and not 1 <= channel <= 16:
            raise MIDIKeyboardError(f"MIDI channel must be between 1 and 16, got {channel}")
        self._selector = device
        self._channel = channel
        self.auto_reconnect = auto_reconnect

        self._device: MIDIDevice | None = None
        self._fd: int | None = None
        self._status: Literal["disconnected", "connected"] = "disconnected"
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._note_on_cb: Callable[[int, int], None] | None = None
        self._note_off_cb: Callable[[int, int], None] | None = None
        self._cc_cb: Callable[[int, int], None] | None = None
        self._pitch_bend_cb: Callable[[int], None] | None = None
        self._aftertouch_cb: Callable[[int], None] | None = None
        self._program_change_cb: Callable[[int], None] | None = None
        self._status_cb: Callable[[str, dict[str, Any]], None] | None = None

        # Parser state (running status, system exclusive skipping)
        self._msg_status: int | None = None
        self._msg_data: list[int] = []
        self._msg_needed = 0
        self._skip = 0
        self._in_sysex = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def status(self) -> Literal["disconnected", "connected"]:
        """``"connected"`` while a device is open, ``"disconnected"`` otherwise."""
        return self._status

    @property
    def name(self) -> str | None:
        """Readable name of the connected device, or None."""
        return self._device.name if self._device else None

    @property
    def device(self) -> MIDIDevice | None:
        """The connected port, or None."""
        return self._device

    @staticmethod
    def list_devices() -> list[MIDIDevice]:
        """Lists the MIDI input ports, USB devices first."""
        return list_midi_inputs()

    @staticmethod
    def note_to_frequency(note: int) -> float:
        return note_to_frequency(note)

    @staticmethod
    def frequency_to_note(frequency: float) -> int:
        return frequency_to_note(frequency)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Opens the device and starts listening.

        Raises:
            MIDIKeyboardOpenError: no matching device and ``auto_reconnect`` is False.
        """
        if self._running.is_set():
            return
        device = self._resolve()
        if device is None and not self.auto_reconnect:
            available = ", ".join(f"{d.name} ({d.hw_name})" for d in list_midi_inputs()) or "none"
            raise MIDIKeyboardOpenError(
                f"MIDI device '{self._selector}' not found (available: {available})",
                hint="Connect the MIDI controller and restart the app.",
            )
        self._running.set()
        if device is not None:
            try:
                self._open(device)
            except OSError as e:
                if not self.auto_reconnect:
                    self._running.clear()
                    raise MIDIKeyboardOpenError(f"Cannot open MIDI device {device.hw_name}: {e}") from e
                logger.warning(f"Cannot open MIDI device {device.hw_name}: {e}, retrying in background")
        else:
            logger.info("No MIDI device connected, waiting for one")
        self._thread = threading.Thread(target=self._run, name="MIDIKeyboard-Listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops listening and releases the device. Safe to call more than once."""
        if not self._running.is_set():
            return
        self._running.clear()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self._close(notify=False)

    def is_started(self) -> bool:
        return self._running.is_set()

    def __enter__(self) -> MIDIKeyboard:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_status_changed(self, callback: Callable[[str, dict[str, Any]], None] | None) -> None:
        """Registers a callback for ``"connected"`` / ``"disconnected"`` transitions.

        The callback receives the new status and a dict with ``name`` and ``hw_name``
        of the device involved. Pass None to remove it.
        """
        self._status_cb = callback

    def on_note_on(self, callback: Callable[[int, int], None] | None) -> None:
        """``callback(note, velocity)``. A note on with velocity 0 is delivered as a note off."""
        self._note_on_cb = callback

    def on_note_off(self, callback: Callable[[int, int], None] | None) -> None:
        """``callback(note, velocity)``."""
        self._note_off_cb = callback

    def on_control_change(self, callback: Callable[[int, int], None] | None) -> None:
        """``callback(control, value)`` with both in 0-127."""
        self._cc_cb = callback

    def on_pitch_bend(self, callback: Callable[[int], None] | None) -> None:
        """``callback(value)`` with value in -8192..8191, 0 at rest."""
        self._pitch_bend_cb = callback

    def on_aftertouch(self, callback: Callable[[int], None] | None) -> None:
        """``callback(pressure)`` for channel pressure, 0-127."""
        self._aftertouch_cb = callback

    def on_program_change(self, callback: Callable[[int], None] | None) -> None:
        """``callback(program)`` with program in 0-127."""
        self._program_change_cb = callback

    # ------------------------------------------------------------------
    # Device handling
    # ------------------------------------------------------------------

    def _resolve(self) -> MIDIDevice | None:
        """Finds the port matching the selector among the plugged devices."""
        devices = list_midi_inputs()
        if not devices:
            return None
        selector = self._selector
        if selector is None:
            return devices[0]
        if isinstance(selector, int):
            index = selector
        elif selector.isdigit():
            index = int(selector)
        elif selector.startswith("usb:"):
            index = int(selector[4:])
        else:
            for d in devices:
                if selector in (d.hw_name, d.path):
                    return d
            needle = selector.lower()
            for d in devices:
                if needle in d.name.lower():
                    return d
            return None
        return devices[index - 1] if 1 <= index <= len(devices) else None

    def _open(self, device: MIDIDevice) -> None:
        fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
        with self._lock:
            self._fd = fd
            self._device = device
            self._reset_parser()
        self._set_status("connected", device)
        logger.info(f"MIDI input opened: {device.name} ({device.hw_name})")

    def _close(self, notify: bool) -> None:
        with self._lock:
            fd, device = self._fd, self._device
            self._fd = None
            self._device = None
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if device is not None:
            self._set_status("disconnected", device, notify=notify)

    def _set_status(self, status: Literal["disconnected", "connected"], device: MIDIDevice, notify: bool = True) -> None:
        if status == self._status:
            return
        self._status = status
        if notify and self._status_cb is not None:
            try:
                self._status_cb(status, {"name": device.name, "hw_name": device.hw_name})
            except Exception as e:
                logger.error(f"Error in status callback: {e}")

    def _run(self) -> None:
        while self._running.is_set():
            fd = self._fd
            if fd is None:
                device = self._resolve()
                if device is not None:
                    try:
                        self._open(device)
                        continue
                    except OSError as e:
                        logger.debug(f"Cannot open {device.hw_name}: {e}")
                time.sleep(self.reconnect_delay)
                continue
            try:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if not ready:
                    continue
                chunk = os.read(fd, 256)
            except OSError as e:
                logger.warning(f"MIDI device disconnected: {e}")
                self._close(notify=True)
                if not self.auto_reconnect:
                    self._running.clear()
                continue
            if not chunk:
                continue
            self._feed(chunk)

    # ------------------------------------------------------------------
    # MIDI byte stream parser
    # ------------------------------------------------------------------

    def _reset_parser(self) -> None:
        self._msg_status = None
        self._msg_data = []
        self._msg_needed = 0
        self._skip = 0
        self._in_sysex = False

    def _feed(self, chunk: bytes) -> None:
        for b in chunk:
            if b >= 0xF8:  # system real-time: clock, start, stop... never part of a message
                continue
            if b == 0xF0:
                self._in_sysex = True
                continue
            if b == 0xF7:
                self._in_sysex = False
                continue
            if self._in_sysex:
                continue
            if b & 0x80:
                if b >= 0xF0:  # system common: cancels running status, data bytes are skipped
                    self._msg_status = None
                    self._skip = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(b, 0)
                    continue
                self._msg_status = b
                self._msg_data = []
                self._msg_needed = 1 if (b & 0xF0) in (0xC0, 0xD0) else 2
                continue
            if self._skip:
                self._skip -= 1
                continue
            if self._msg_status is None:  # data byte without a status: stream started mid-message
                continue
            self._msg_data.append(b)
            if len(self._msg_data) == self._msg_needed:
                self._dispatch(self._msg_status, self._msg_data)
                self._msg_data = []  # running status: the next data bytes reuse the same status

    def _dispatch(self, status: int, data: list[int]) -> None:
        if self._channel is not None and (status & 0x0F) + 1 != self._channel:
            return
        kind = status & 0xF0
        if kind == 0x90 and data[1] > 0:
            self._call(self._note_on_cb, data[0], data[1])
        elif kind in (0x80, 0x90):
            self._call(self._note_off_cb, data[0], data[1])
        elif kind == 0xB0:
            self._call(self._cc_cb, data[0], data[1])
        elif kind == 0xE0:
            self._call(self._pitch_bend_cb, ((data[1] << 7) | data[0]) - 8192)
        elif kind == 0xD0:
            self._call(self._aftertouch_cb, data[0])
        elif kind == 0xC0:
            self._call(self._program_change_cb, data[0])
        # 0xA0 polyphonic aftertouch is read and ignored

    @staticmethod
    def _call(callback: Callable[..., None] | None, *args: int) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as e:
            logger.error(f"Error in MIDI callback: {e}")
