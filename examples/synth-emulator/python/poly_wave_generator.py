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

# ADSR envelope stages
_ADSR_IDLE = 0
_ADSR_ATTACK = 1
_ADSR_DECAY = 2
_ADSR_SUSTAIN = 3
_ADSR_RELEASE = 4


# ---------------------------------------------------------------------------
# Schroeder reverb (4 comb + 2 allpass filters)
# ---------------------------------------------------------------------------


class _ReverbState:
    """Simple Schroeder reverb — 4 parallel comb filters + 2 series allpass filters."""

    _COMB_DELAYS_MS = (29.7, 37.1, 41.1, 43.7)
    _ALLPASS_DELAYS_MS = (5.0, 1.7)
    _FEEDBACK = 0.84
    _DAMP = 0.2

    def __init__(self, sample_rate: int) -> None:
        def _buf(ms):
            n = max(1, int(ms / 1000.0 * sample_rate))
            return {"buf": np.zeros(n, dtype=np.float32), "pos": 0, "filt": 0.0}

        self._combs = [_buf(d) for d in self._COMB_DELAYS_MS]
        self._allpasses = [_buf(d) for d in self._ALLPASS_DELAYS_MS]

    def process(self, buf: np.ndarray, wet: float) -> None:
        if wet == 0.0:
            return
        n = len(buf)
        out = np.zeros(n, dtype=np.float32)
        fb = self._FEEDBACK
        damp = self._DAMP
        for c in self._combs:
            cb, size, pos, filt = c["buf"], len(c["buf"]), c["pos"], c["filt"]
            for i in range(n):
                output = cb[pos]
                filt = output * (1.0 - damp) + filt * damp
                cb[pos] = buf[i] + filt * fb
                pos = (pos + 1) % size
                out[i] += output
            c["pos"] = pos
            c["filt"] = filt
        out *= np.float32(0.25)
        for ap in self._allpasses:
            ab, size, pos = ap["buf"], len(ap["buf"]), ap["pos"]
            for i in range(n):
                buf_val = ab[pos]
                ab[pos] = out[i] + 0.5 * buf_val
                out[i] = buf_val - 0.5 * out[i]
                pos = (pos + 1) % size
            ap["pos"] = pos
        buf *= np.float32(1.0 - wet)
        buf += out * np.float32(wet)


# ---------------------------------------------------------------------------
# Per-voice synthesis state
# ---------------------------------------------------------------------------


class _VoiceState:
    """All state required to render one oscillator voice."""

    __slots__ = [
        "note",
        "freq",
        "amp",
        "prev_freq",
        "prev_amp",
        "prev_phase",
        "freq_glide_start",
        "freq_glide_target",
        "freq_glide_elapsed",
        "amp_ramp_start",
        "amp_ramp_target",
        "amp_ramp_duration",
        "amp_ramp_elapsed",
        "adsr_stage",
        "peak_amp",
        "buf_phases",
        "buf_samples",
    ]

    def __init__(self):
        self.note = None  # MIDI note number (int) or None when idle
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
        self.adsr_stage = _ADSR_IDLE
        self.peak_amp = 0.0
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
        wave_gen.voices = 2  # enable 2-voice polyphony
        wave_gen.note_on(60, 100)  # middle C
        wave_gen.note_on(64, 90)  # E above middle C
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
        self._decay = 0.1
        self._sustain = 0.8
        self._release = float(release)
        self._glide = float(glide)
        self._active_voices = 1

        # Effects parameters
        self._cutoff = float(_SAMPLE_RATE) / 2.0  # fully open (Hz)
        self._resonance = 0.0  # 0.0–1.0
        self._overdrive = 0.0  # 0.0–1.0
        self._tremolo_depth = 0.0  # 0.0–1.0
        self._tremolo_rate = 5.0  # Hz
        self._delay_time = 0.0  # seconds (0 = off)
        self._delay_feedback = 0.5  # 0.0–0.95
        self._reverb_wet = 0.0  # 0.0–1.0

        # Filter state (biquad DF2T, recalculated when cutoff/resonance change)
        self._filter_b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
        self._filter_a12 = np.array([0.0, 0.0], dtype=np.float64)
        self._filter_z = np.zeros(2, dtype=np.float64)
        self._filter_dirty = True

        # Tremolo state
        self._tremolo_phase = 0.0

        # Delay state (ring buffer, max 1 second)
        self._delay_buf = np.zeros(int(_SAMPLE_RATE), dtype=np.float32)
        self._delay_pos = 0

        # Reverb
        self._reverb = _ReverbState(_SAMPLE_RATE)

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
        for v in self._voices[: self._active_voices]:
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

        # Reset amplitude envelope: start ADSR from silence
        v.peak_amp = max(0.0, min(1.0, velocity / 127.0))
        v.adsr_stage = _ADSR_ATTACK
        v.prev_amp = 0.0
        v.amp_ramp_start = 0.0
        v.amp_ramp_target = 0.0
        v.amp_ramp_elapsed = 0.0
        v.amp = v.peak_amp  # attack target

        self._voice_queue.append(idx)
        logger.debug(f"Voice {idx} → note {note} ({new_freq:.1f} Hz)")

    def note_off(self, note: int) -> None:
        """Trigger release envelope for the given note."""
        for i, v in enumerate(self._voices[: self._active_voices]):
            if v.note == note:
                v.adsr_stage = _ADSR_RELEASE
                v.amp = 0.0  # release target
                v.note = None  # free slot for new note_on
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
        for v in self._voices[: self._active_voices]:
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
        return any(v.note is not None for v in self._voices[: self._active_voices])

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
        self._attack = max(0.0, float(val))

    @property
    def decay(self) -> float:
        return self._decay

    @decay.setter
    def decay(self, val: float) -> None:
        self._decay = max(0.0, float(val))

    @property
    def sustain(self) -> float:
        return self._sustain

    @sustain.setter
    def sustain(self, val: float) -> None:
        self._sustain = max(0.0, min(1.0, float(val)))

    @property
    def release(self) -> float:
        return self._release

    @release.setter
    def release(self, val: float) -> None:
        self._release = max(0.0, float(val))

    @property
    def glide(self) -> float:
        return self._glide

    @glide.setter
    def glide(self, val: float) -> None:
        self._glide = max(0.0, float(val))

    @property
    def cutoff(self) -> float:
        return self._cutoff

    @cutoff.setter
    def cutoff(self, val: float) -> None:
        val = max(20.0, min(float(val), float(_SAMPLE_RATE) / 2.0))
        if val != self._cutoff:
            self._cutoff = val
            self._filter_dirty = True

    @property
    def resonance(self) -> float:
        return self._resonance

    @resonance.setter
    def resonance(self, val: float) -> None:
        val = max(0.0, min(1.0, float(val)))
        if val != self._resonance:
            self._resonance = val
            self._filter_dirty = True

    @property
    def overdrive(self) -> float:
        return self._overdrive

    @overdrive.setter
    def overdrive(self, val: float) -> None:
        self._overdrive = max(0.0, min(1.0, float(val)))

    @property
    def tremolo_depth(self) -> float:
        return self._tremolo_depth

    @tremolo_depth.setter
    def tremolo_depth(self, val: float) -> None:
        self._tremolo_depth = max(0.0, min(1.0, float(val)))

    @property
    def tremolo_rate(self) -> float:
        return self._tremolo_rate

    @tremolo_rate.setter
    def tremolo_rate(self, val: float) -> None:
        self._tremolo_rate = max(0.1, min(20.0, float(val)))

    @property
    def delay_time(self) -> float:
        return self._delay_time

    @delay_time.setter
    def delay_time(self, val: float) -> None:
        self._delay_time = max(0.0, min(1.0, float(val)))

    @property
    def delay_feedback(self) -> float:
        return self._delay_feedback

    @delay_feedback.setter
    def delay_feedback(self, val: float) -> None:
        self._delay_feedback = max(0.0, min(0.95, float(val)))

    @property
    def reverb_wet(self) -> float:
        return self._reverb_wet

    @reverb_wet.setter
    def reverb_wet(self, val: float) -> None:
        self._reverb_wet = max(0.0, min(1.0, float(val)))

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
            for v in self._voices[: self._active_voices]:
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
            "decay": self._decay,
            "sustain": self._sustain,
            "release": self._release,
            "glide": self._glide,
            "volume": self.volume,
            "voices": self._active_voices,
            "cutoff": self._cutoff,
            "resonance": self._resonance,
            "overdrive": self._overdrive,
            "tremolo_depth": self._tremolo_depth,
            "tremolo_rate": self._tremolo_rate,
            "delay_time": self._delay_time,
            "delay_feedback": self._delay_feedback,
            "reverb_wet": self._reverb_wet,
        }

    # ------------------------------------------------------------------
    # Effects chain
    # ------------------------------------------------------------------

    def _update_filter_coeffs(self) -> None:
        fc = max(20.0, min(self._cutoff, float(_SAMPLE_RATE) / 2.0 - 1.0))
        q = 0.707 + self._resonance * 9.293  # Butterworth (0.707) → resonant (10.0)
        w0 = 2.0 * np.pi * fc / float(_SAMPLE_RATE)
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * q)
        a0 = 1.0 + alpha
        self._filter_b = np.array(
            [
                (1.0 - cos_w0) / 2.0 / a0,
                (1.0 - cos_w0) / a0,
                (1.0 - cos_w0) / 2.0 / a0,
            ],
            dtype=np.float64,
        )
        self._filter_a12 = np.array(
            [
                (-2.0 * cos_w0) / a0,
                (1.0 - alpha) / a0,
            ],
            dtype=np.float64,
        )
        self._filter_dirty = False

    def _apply_filter(self, buf: np.ndarray) -> None:
        if self._cutoff >= float(_SAMPLE_RATE) / 2.0 - 100.0 and self._resonance == 0.0:
            return  # filter fully open — skip
        if self._filter_dirty:
            self._update_filter_coeffs()
        b0, b1, b2 = self._filter_b
        a1, a2 = self._filter_a12
        z1, z2 = float(self._filter_z[0]), float(self._filter_z[1])
        for i in range(len(buf)):
            x = float(buf[i])
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            buf[i] = np.float32(y)
        self._filter_z[0] = z1
        self._filter_z[1] = z2

    def _apply_overdrive(self, buf: np.ndarray) -> None:
        if self._overdrive == 0.0:
            return
        drive = np.float32(1.0 + self._overdrive * 9.0)  # 1× → 10×
        np.multiply(buf, drive, out=buf)
        np.tanh(buf, out=buf)
        # Compensate output level so unity gain at low drive
        np.multiply(buf, np.float32(1.0 / float(np.tanh(drive))), out=buf)

    def _apply_tremolo(self, buf: np.ndarray) -> None:
        if self._tremolo_depth == 0.0:
            return
        n = len(buf)
        inc = np.float32(2.0 * np.pi * self._tremolo_rate / float(_SAMPLE_RATE))
        lfo = np.arange(n, dtype=np.float32) * inc + np.float32(self._tremolo_phase)
        np.sin(lfo, out=lfo)
        depth = np.float32(self._tremolo_depth)
        # LFO modulates amplitude: 1.0 at top, (1 - depth) at bottom
        np.multiply(lfo, depth * np.float32(0.5), out=lfo)
        np.add(lfo, np.float32(1.0) - depth * np.float32(0.5), out=lfo)
        np.multiply(buf, lfo, out=buf)
        self._tremolo_phase = float((self._tremolo_phase + n * inc) % (2.0 * np.pi))

    def _apply_delay(self, buf: np.ndarray) -> None:
        if self._delay_time == 0.0:
            return
        delay_samples = min(int(self._delay_time * _SAMPLE_RATE), len(self._delay_buf) - 1)
        if delay_samples == 0:
            return
        db = self._delay_buf
        buf_len = len(db)
        pos = self._delay_pos
        fb = np.float32(self._delay_feedback)
        for i in range(len(buf)):
            read_pos = (pos - delay_samples) % buf_len
            echo = db[read_pos]
            db[pos] = buf[i] + echo * fb
            buf[i] += echo
            pos = (pos + 1) % buf_len
        self._delay_pos = pos

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

        # Effects chain (applied to the final mixed signal)
        if contributing > 0:
            self._apply_overdrive(mix)
            self._apply_filter(mix)
            self._apply_tremolo(mix)
            self._apply_delay(mix)
            self._reverb.process(mix, self._reverb_wet)

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

        stage = v.adsr_stage
        finish_stage = False

        if prev_amp == amplitude:
            amp_start = amplitude
            amp_end = amplitude
        else:
            if amplitude != v.amp_ramp_target:
                # New target: choose ramp duration from ADSR stage
                v.amp_ramp_start = prev_amp
                v.amp_ramp_target = amplitude
                v.amp_ramp_elapsed = 0.0
                if stage == _ADSR_ATTACK:
                    v.amp_ramp_duration = attack
                elif stage == _ADSR_DECAY:
                    v.amp_ramp_duration = self._decay
                else:  # RELEASE or fallback
                    v.amp_ramp_duration = release

            ramp_duration = v.amp_ramp_duration
            if ramp_duration <= 0.0:
                amp_start = amplitude
                amp_end = amplitude
                v.amp_ramp_elapsed = 0.0
                finish_stage = True
            else:
                elapsed = v.amp_ramp_elapsed
                progress_start = min(elapsed / ramp_duration, 1.0)
                progress_end = min((elapsed + block_duration) / ramp_duration, 1.0)
                amp_start = v.amp_ramp_start + (v.amp_ramp_target - v.amp_ramp_start) * progress_start
                amp_end = v.amp_ramp_start + (v.amp_ramp_target - v.amp_ramp_start) * progress_end
                v.amp_ramp_elapsed += block_duration
                finish_stage = progress_end >= 1.0

        # ADSR stage auto-advance
        if finish_stage:
            if stage == _ADSR_ATTACK:
                sustain_level = v.peak_amp * self._sustain
                v.adsr_stage = _ADSR_DECAY
                v.amp = sustain_level
                v.amp_ramp_start = v.peak_amp
                v.amp_ramp_target = sustain_level
                v.amp_ramp_elapsed = 0.0
                v.amp_ramp_duration = self._decay
            elif stage == _ADSR_DECAY:
                v.adsr_stage = _ADSR_SUSTAIN

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
