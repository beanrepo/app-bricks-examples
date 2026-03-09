# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

"""Polyphonic wave generator for the synth-emulator example.

Implements a polyphonic synthesizer with up to 3 simultaneous voices sharing
a single ALSA speaker output. Audio from all active voices is summed in
software and written to the device as a single stream.
"""

import threading
from collections import deque

import numpy as np

from arduino.app_peripherals.speaker import ALSASpeaker, Speaker
from arduino.app_utils import Logger, brick
from midi_keyboard import MIDIKeyboard

logger = Logger("PolyWaveGenerator")

# Audio constants — mirror WaveGenerator defaults
_BUFFER_SIZE = Speaker.BUFFER_SIZE_REALTIME  # 256 frames
_BLOCK_SIZE = max(32, min(_BUFFER_SIZE, _BUFFER_SIZE // 4))  # 64 frames
_SAMPLE_RATE = Speaker.RATE_48K  # 48 000 Hz
_BLOCK_DURATION = _BLOCK_SIZE / float(_SAMPLE_RATE)  # ~1.33 ms per block


# ---------------------------------------------------------------------------
# Per-voice synthesis state
# ---------------------------------------------------------------------------

class _VoiceState:
    """All state required to render one oscillator voice."""

    __slots__ = [
        "note",
        "freq", "amp",
        "prev_freq", "prev_amp", "prev_phase",
        "freq_glide_start", "freq_glide_target", "freq_glide_elapsed",
        "amp_ramp_start", "amp_ramp_target", "amp_ramp_duration", "amp_ramp_elapsed",
        "buf_phases", "buf_samples",
    ]

    def __init__(self):
        self.note = None         # MIDI note number (int) or None when idle
        self.freq = 440.0
        self.amp = 0.0
        self.prev_freq = 440.0
        self.prev_amp = 0.0
        self.prev_phase = np.float32(0.0)
        self.freq_glide_start = 440.0
        self.freq_glide_target = 440.0
        self.freq_glide_elapsed = 0.0
        self.amp_ramp_start = 0.0
        self.amp_ramp_target = 0.0
        self.amp_ramp_duration = 0.0
        self.amp_ramp_elapsed = 0.0
        self.buf_phases = np.zeros(_BLOCK_SIZE, dtype=np.float32)
        self.buf_samples = np.zeros(_BLOCK_SIZE, dtype=np.float32)


# ---------------------------------------------------------------------------
# PolyWaveGenerator
# ---------------------------------------------------------------------------

@brick
class PolyWaveGenerator:
    """Polyphonic wave generator — up to 3 simultaneous voices, single speaker output.

    Manages a pool of oscillator voices. Incoming note_on events are assigned to
    free voices using a round-robin allocator with oldest-voice stealing. All active
    voices are summed into one audio stream that is written to the ALSA device.

    Shared synthesis parameters (wave_type, attack, release, glide, volume) apply to
    all voices simultaneously. Per-voice frequency and amplitude are set by note events.

    Usage::

        wave_gen = PolyWaveGenerator(wave_type="sine", attack=0.01, release=0.1)
        wave_gen.voices = 2             # enable 2-voice polyphony
        wave_gen.note_on(60, 100)       # middle C
        wave_gen.note_on(64, 90)        # E above middle C
        wave_gen.note_off(60)
    """

    MAX_VOICES = 3

    def __init__(
        self,
        wave_type: str = "sine",
        attack: float = 0.01,
        release: float = 0.1,
        glide: float = 0.0,
    ):
        self._wave_type = wave_type
        self._attack = float(attack)
        self._release = float(release)
        self._glide = float(glide)
        self._active_voices = 1

        # Single speaker shared by all voices
        self._speaker = ALSASpeaker(
            device=Speaker.USB_SPEAKER_1,
            sample_rate=_SAMPLE_RATE,
            channels=Speaker.CHANNELS_MONO,
            format=np.float32,
            buffer_size=_BUFFER_SIZE,
            shared=False,
        )

        # Voice pool
        self._voices = [_VoiceState() for _ in range(self.MAX_VOICES)]
        self._voice_queue: deque = deque()  # indices of voices in use, oldest first
        self._bend_factor = 1.0

        # Pre-allocated audio buffers
        self._sample_indices = np.arange(1, _BLOCK_SIZE + 1, dtype=np.float32)
        self._ramp_vec = np.linspace(0.0, 1.0, _BLOCK_SIZE, dtype=np.float32)
        self._buf_mix = np.zeros(_BLOCK_SIZE, dtype=np.float32)
        self._buf_envelope = np.zeros(_BLOCK_SIZE, dtype=np.float32)

        self._two_pi = np.float32(2.0 * np.pi)
        self._running = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._speaker.start()
        self._running.set()

    def stop(self) -> None:
        self._running.clear()
        self._speaker.stop()

    @brick.execute
    def _generator_loop(self) -> None:
        while self._running.is_set():
            buf = self._generate_poly_block()
            self._speaker.play(buf)

    # ------------------------------------------------------------------
    # Voice allocation
    # ------------------------------------------------------------------

    def note_on(self, note: int, velocity: int) -> None:
        """Assign a note to a free voice (or steal the oldest) and trigger attack."""
        # Retrigger if the same note is already playing on a voice
        for v in self._voices[:self._active_voices]:
            if v.note == note:
                v.amp = max(0.0, min(1.0, velocity / 127.0))
                logger.debug(f"Retriggered note {note}")
                return

        idx = self._alloc_voice()
        v = self._voices[idx]
        v.note = note

        new_freq = MIDIKeyboard.note_to_frequency(note) * self._bend_factor
        # Reset frequency glide state so voice starts exactly on the target pitch
        v.freq = new_freq
        v.prev_freq = new_freq
        v.freq_glide_start = new_freq
        v.freq_glide_target = new_freq
        v.freq_glide_elapsed = 0.0

        # Reset amplitude envelope state so voice always starts from silence
        v.prev_amp = 0.0
        v.amp_ramp_start = 0.0
        v.amp_ramp_target = 0.0
        v.amp_ramp_elapsed = 0.0
        v.amp = max(0.0, min(1.0, velocity / 127.0))

        self._voice_queue.append(idx)
        logger.debug(f"Voice {idx} → note {note} ({new_freq:.1f} Hz)")

    def note_off(self, note: int) -> None:
        """Release the voice playing the given note (triggers release envelope)."""
        for i, v in enumerate(self._voices[:self._active_voices]):
            if v.note == note:
                v.amp = 0.0
                v.note = None
                try:
                    self._voice_queue.remove(i)
                except ValueError:
                    pass
                logger.debug(f"Released note {note} (voice {i})")
                break

    def all_notes_off(self) -> None:
        """Immediately silence all voices."""
        for v in self._voices:
            v.amp = 0.0
            v.note = None
        self._voice_queue.clear()

    def set_pitch_bend(self, bend_factor: float) -> None:
        """Apply a pitch-bend multiplier to all currently playing voices."""
        self._bend_factor = bend_factor
        for v in self._voices[:self._active_voices]:
            if v.note is not None:
                v.freq = MIDIKeyboard.note_to_frequency(v.note) * bend_factor

    def _alloc_voice(self) -> int:
        """Return a free voice index, stealing the oldest active voice if necessary."""
        for i in range(self._active_voices):
            if self._voices[i].note is None:
                return i
        # Voice steal: reclaim the voice that has been playing longest
        if self._voice_queue:
            oldest = self._voice_queue.popleft()
            self._voices[oldest].note = None
            return oldest
        return 0

    # ------------------------------------------------------------------
    # Properties — shared across all voices
    # ------------------------------------------------------------------

    @property
    def voices(self) -> int:
        """Number of simultaneously playable voices (1–3)."""
        return self._active_voices

    @voices.setter
    def voices(self, n: int) -> None:
        n = max(1, min(self.MAX_VOICES, n))
        if n < self._active_voices:
            # Silence voices that exceed the new limit
            for i in range(n, self._active_voices):
                self._voices[i].amp = 0.0
                self._voices[i].note = None
                try:
                    self._voice_queue.remove(i)
                except ValueError:
                    pass
        self._active_voices = n
        logger.info(f"Polyphony set to {n} voice(s)")

    @property
    def has_active_notes(self) -> bool:
        return any(v.note is not None for v in self._voices[:self._active_voices])

    @property
    def wave_type(self) -> str:
        return self._wave_type

    @wave_type.setter
    def wave_type(self, wt: str) -> None:
        valid = ("sine", "square", "sawtooth", "triangle")
        if wt not in valid:
            raise ValueError(f"Invalid wave_type '{wt}'. Must be one of {valid}")
        self._wave_type = wt

    @property
    def attack(self) -> float:
        return self._attack

    @attack.setter
    def attack(self, val: float) -> None:
        self._attack = float(val)

    @property
    def release(self) -> float:
        return self._release

    @release.setter
    def release(self, val: float) -> None:
        self._release = float(val)

    @property
    def glide(self) -> float:
        return self._glide

    @glide.setter
    def glide(self, val: float) -> None:
        self._glide = float(val)

    @property
    def volume(self) -> int:
        return self._speaker.volume

    @volume.setter
    def volume(self, val: int) -> None:
        self._speaker.volume = val

    @property
    def frequency(self) -> float:
        """Frequency of voice 0 (useful in mono mode / for UI display)."""
        return self._voices[0].freq

    @frequency.setter
    def frequency(self, freq: float) -> None:
        """In mono mode sets voice 0 directly; in poly sets all playing voices."""
        if self._active_voices == 1:
            self._voices[0].freq = freq
        else:
            for v in self._voices[:self._active_voices]:
                if v.note is not None:
                    v.freq = freq

    @property
    def amplitude(self) -> float:
        """Amplitude of voice 0 (for backward-compatible UI slider and display)."""
        return self._voices[0].amp

    @amplitude.setter
    def amplitude(self, amp: float) -> None:
        self._voices[0].amp = max(0.0, min(1.0, amp))

    @property
    def state(self) -> dict:
        return {
            "frequency": self.frequency,
            "amplitude": self.amplitude,
            "wave_type": self._wave_type,
            "attack": self._attack,
            "release": self._release,
            "glide": self._glide,
            "volume": self.volume,
            "voices": self._active_voices,
        }

    # ------------------------------------------------------------------
    # Audio synthesis
    # ------------------------------------------------------------------

    def _generate_poly_block(self) -> np.ndarray:
        """Sum all active voices into one audio block."""
        mix = self._buf_mix
        mix.fill(0.0)

        active_count = self._active_voices
        contributing = 0

        for i in range(active_count):
            v = self._voices[i]
            # Skip voice if it's fully silent (no output pending)
            if v.amp == 0.0 and v.prev_amp == 0.0:
                continue
            self._generate_voice_block(v)
            mix += v.buf_samples
            contributing += 1

        # Normalise by active voice count to prevent clipping
        if active_count > 1 and contributing > 0:
            mix /= active_count

        return mix

    def _generate_voice_block(self, v: _VoiceState) -> None:
        """Render one audio block for voice *v*, writing result into v.buf_samples."""
        block_size = _BLOCK_SIZE
        block_duration = _BLOCK_DURATION
        sample_rate = float(_SAMPLE_RATE)
        two_pi = self._two_pi
        ramp_vec = self._ramp_vec
        sample_indices = self._sample_indices

        buf_phases = v.buf_phases
        buf_samples = v.buf_samples
        buf_envelope = self._buf_envelope  # shared; safe because voices are processed sequentially

        frequency = v.freq
        amplitude = v.amp
        wave_type = self._wave_type
        glide = self._glide
        attack = self._attack
        release = self._release

        # ---- FREQUENCY & PHASE -----------------------------------------------
        current_freq = v.prev_freq

        if current_freq == frequency:
            # Constant frequency: simple phase increment
            inc = np.float32((frequency * two_pi) / sample_rate)
            np.multiply(sample_indices, inc, out=buf_phases)
            np.add(buf_phases, v.prev_phase, out=buf_phases)
            v.prev_phase = buf_phases[-1] % two_pi
        else:
            # Frequency is changing
            if frequency != v.freq_glide_target:
                # New glide target: restart glide from current rendered frequency
                v.freq_glide_start = current_freq
                v.freq_glide_target = frequency
                v.freq_glide_elapsed = 0.0

            if glide <= 0.0:
                # Instant jump
                inc = np.float32((frequency * two_pi) / sample_rate)
                np.multiply(sample_indices, inc, out=buf_phases)
                np.add(buf_phases, v.prev_phase, out=buf_phases)
                current_freq = frequency
                v.freq_glide_elapsed = 0.0
            else:
                # Linear glide interpolation
                elapsed = v.freq_glide_elapsed
                progress_start = min(elapsed / glide, 1.0)
                progress_end = min((elapsed + block_duration) / glide, 1.0)
                freq_start = v.freq_glide_start + (v.freq_glide_target - v.freq_glide_start) * progress_start
                freq_end = v.freq_glide_start + (v.freq_glide_target - v.freq_glide_start) * progress_end

                # buf_phases temporarily holds per-sample frequencies
                np.subtract(freq_end, freq_start, out=buf_phases)
                np.multiply(buf_phases, ramp_vec, out=buf_phases)
                np.add(buf_phases, freq_start, out=buf_phases)
                # Convert Hz → phase increment, then accumulate
                np.multiply(buf_phases, two_pi / sample_rate, out=buf_phases)
                np.cumsum(buf_phases, out=buf_phases)
                np.add(buf_phases, v.prev_phase, out=buf_phases)

                current_freq = freq_end
                v.freq_glide_elapsed += block_duration

            v.prev_freq = current_freq
            v.prev_phase = buf_phases[-1] % two_pi

        # Wrap phases to [0, 2π) to prevent floating-point drift
        np.mod(buf_phases, two_pi, out=buf_phases)

        # ---- AMPLITUDE ENVELOPE ----------------------------------------------
        prev_amp = v.prev_amp

        if prev_amp == amplitude:
            amp_start = amplitude
            amp_end = amplitude
        else:
            if amplitude != v.amp_ramp_target:
                # New amplitude target: start a fresh ramp
                v.amp_ramp_start = prev_amp
                v.amp_ramp_target = amplitude
                v.amp_ramp_elapsed = 0.0
                v.amp_ramp_duration = attack if amplitude > prev_amp else release

            ramp_duration = v.amp_ramp_duration
            if ramp_duration <= 0.0:
                amp_start = amplitude
                amp_end = amplitude
                v.amp_ramp_elapsed = 0.0
            else:
                elapsed = v.amp_ramp_elapsed
                progress_start = min(elapsed / ramp_duration, 1.0)
                progress_end = min((elapsed + block_duration) / ramp_duration, 1.0)
                amp_start = v.amp_ramp_start + (v.amp_ramp_target - v.amp_ramp_start) * progress_start
                amp_end = v.amp_ramp_start + (v.amp_ramp_target - v.amp_ramp_start) * progress_end
                v.amp_ramp_elapsed += block_duration

        if amp_start == 0.0 and amp_end == 0.0:
            buf_samples.fill(0.0)
            v.prev_amp = amp_end
            return

        # ---- WAVEFORM GENERATION ---------------------------------------------
        if wave_type == "sine":
            np.sin(buf_phases, out=buf_samples)
        elif wave_type == "square":
            np.sin(buf_phases, out=buf_samples)
            np.sign(buf_samples, out=buf_samples)
        elif wave_type == "sawtooth":
            np.multiply(buf_phases, np.float32(1.0 / two_pi), out=buf_samples)
            np.multiply(buf_samples, np.float32(2.0), out=buf_samples)
            np.subtract(buf_samples, np.float32(1.0), out=buf_samples)
        elif wave_type == "triangle":
            np.multiply(buf_phases, np.float32(1.0 / two_pi), out=buf_samples)
            np.subtract(buf_samples, np.float32(0.5), out=buf_samples)
            np.abs(buf_samples, out=buf_samples)
            np.multiply(buf_samples, np.float32(4.0), out=buf_samples)
            np.subtract(buf_samples, np.float32(1.0), out=buf_samples)
        else:
            np.sin(buf_phases, out=buf_samples)

        # ---- APPLY AMPLITUDE ENVELOPE ----------------------------------------
        if amp_start == amp_end:
            if amp_start != 1.0:
                np.multiply(buf_samples, np.float32(amp_start), out=buf_samples)
        else:
            np.subtract(amp_end, amp_start, out=buf_envelope)
            np.multiply(buf_envelope, ramp_vec, out=buf_envelope)
            np.add(buf_envelope, amp_start, out=buf_envelope)
            np.multiply(buf_samples, buf_envelope, out=buf_samples)

        v.prev_amp = amp_end
