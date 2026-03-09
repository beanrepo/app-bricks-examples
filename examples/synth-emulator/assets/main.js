/*
 * SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
 *
 * SPDX-License-Identifier: MPL-2.0
 */

(function () {
  const socket = io({ transports: ['websocket'] });

  // UI Elements
  const waveformBtns = document.querySelectorAll('.waveform-btn');
  const voicesBtns = document.querySelectorAll('.voices-btn');
  const attackSlider = document.getElementById('attack-slider');
  const decaySlider = document.getElementById('decay-slider');
  const sustainSlider = document.getElementById('sustain-slider');
  const releaseSlider = document.getElementById('release-slider');
  const glideSlider = document.getElementById('glide-slider');
  const frequencySlider = document.getElementById('frequency-slider');
  const amplitudeSlider = document.getElementById('amplitude-slider');
  const volumeSlider = document.getElementById('volume-slider');

  const attackValue = document.getElementById('attack-value');
  const decayValue = document.getElementById('decay-value');
  const sustainValue = document.getElementById('sustain-value');
  const releaseValue = document.getElementById('release-value');
  const glideValue = document.getElementById('glide-value');
  const frequencyValue = document.getElementById('frequency-value');
  const amplitudeValue = document.getElementById('amplitude-value');
  const volumeValue = document.getElementById('volume-value');

  // Effects sliders
  const cutoffSlider = document.getElementById('cutoff-slider');
  const resonanceSlider = document.getElementById('resonance-slider');
  const overdriveSlider = document.getElementById('overdrive-slider');
  const tremoloDepthSlider = document.getElementById('tremolo-depth-slider');
  const tremoloRateSlider = document.getElementById('tremolo-rate-slider');
  const delayTimeSlider = document.getElementById('delay-time-slider');
  const delayFeedbackSlider = document.getElementById('delay-feedback-slider');
  const reverbSlider = document.getElementById('reverb-slider');

  const cutoffValue = document.getElementById('cutoff-value');
  const resonanceValue = document.getElementById('resonance-value');
  const overdriveValue = document.getElementById('overdrive-value');
  const tremoloDepthValue = document.getElementById('tremolo-depth-value');
  const tremoloRateValue = document.getElementById('tremolo-rate-value');
  const delayTimeValue = document.getElementById('delay-time-value');
  const delayFeedbackValue = document.getElementById('delay-feedback-value');
  const reverbValue = document.getElementById('reverb-value');

  const keyboardKeys = document.querySelectorAll('.key');
  const midiStatus = document.getElementById('midi-status');
  const midiLog = document.getElementById('midi-log');
  const midiIndicator = document.getElementById('midi-indicator');

  const statusFrequency = document.getElementById('status-frequency');
  const statusAmplitude = document.getElementById('status-amplitude');
  const statusWaveform = document.getElementById('status-waveform');
  const statusVoices = document.getElementById('status-voices');

  const learnBtns = document.querySelectorAll('.learn-btn');
  const clearBtns = document.querySelectorAll('.clear-btn');

  // State
  let ccMapping = {};
  let currentLearnParam = null;
  let midiAvailable = false;
  let midiIndicatorTimeout = null;

  // Throttle slider input events — mirrors the Python-side MIDI pitch-bend throttle.
  // The label display is updated immediately; only the socket.emit is rate-limited.
  // A companion 'change' listener guarantees the final value is sent on mouse/touch release.
  const _SLIDER_THROTTLE_MS = 25; // max 40 messages/sec per slider
  function throttle(fn, ms) {
    let lastTime = 0;
    return function (...args) {
      const now = Date.now();
      if (now - lastTime >= ms) { lastTime = now; fn.apply(this, args); }
    };
  }

  // --- Socket.IO Event Handlers ---

  socket.on('connect', () => {
    console.log('Connected to server');
  });

  socket.on('synth:state', (data) => {
    console.log('State update:', data);

    if (data.frequency !== undefined) {
      statusFrequency.textContent = `${data.frequency.toFixed(1)} Hz`;
      // Update slider always (including MIDI source)
      frequencySlider.value = Math.round(data.frequency);
      frequencyValue.textContent = Math.round(data.frequency);
    }

    if (data.amplitude !== undefined) {
      statusAmplitude.textContent = data.amplitude.toFixed(2);
      // Update slider always (including MIDI source)
      amplitudeSlider.value = Math.round(data.amplitude * 100);
      amplitudeValue.textContent = data.amplitude.toFixed(2);
    }

    if (data.waveform !== undefined) {
      statusWaveform.textContent = data.waveform;
      // Update waveform selector always (including MIDI source)
      setActiveWaveform(data.waveform);
    }

    if (data.voices !== undefined) {
      statusVoices.textContent = data.voices;
      setActiveVoices(data.voices);
    }

    if (data.volume !== undefined) {
      // Update volume slider always (including MIDI source)
      volumeSlider.value = data.volume;
      volumeValue.textContent = data.volume;
    }

    // Update envelope sliders (always update when from MIDI)
    if (data.attack !== undefined) {
      const attack = Math.round(data.attack);
      attackSlider.value = attack;
      attackValue.textContent = attack;
    }
    if (data.release !== undefined) {
      const release = Math.round(data.release);
      releaseSlider.value = release;
      releaseValue.textContent = release;
    }
    if (data.glide !== undefined) {
      const glide = Math.round(data.glide);
      glideSlider.value = glide;
      glideValue.textContent = glide;
    }
    
    // Legacy envelope format support
    if (data.envelope) {
      if (data.envelope.attack !== undefined && data.attack === undefined) {
        const attack = Math.round(data.envelope.attack);
        if (!data.source) { attackSlider.value = attack; attackValue.textContent = attack; }
      }
      if (data.envelope.decay !== undefined) {
        const decay = Math.round(data.envelope.decay);
        decaySlider.value = decay; decayValue.textContent = decay;
      }
      if (data.envelope.sustain !== undefined) {
        const sustain = Math.round(data.envelope.sustain);
        sustainSlider.value = sustain; sustainValue.textContent = sustain;
      }
      if (data.envelope.release !== undefined && data.release === undefined) {
        const release = Math.round(data.envelope.release);
        if (!data.source) { releaseSlider.value = release; releaseValue.textContent = release; }
      }
      if (data.envelope.glide !== undefined && data.glide === undefined) {
        const glide = Math.round(data.envelope.glide);
        if (!data.source) { glideSlider.value = glide; glideValue.textContent = glide; }
      }
    }

    // Effects state
    if (data.effects) {
      const e = data.effects;
      if (e.cutoff !== undefined) { cutoffSlider.value = Math.round(e.cutoff); cutoffValue.textContent = Math.round(e.cutoff); }
      if (e.resonance !== undefined) { resonanceSlider.value = Math.round(e.resonance); resonanceValue.textContent = Math.round(e.resonance); }
      if (e.overdrive !== undefined) { overdriveSlider.value = Math.round(e.overdrive); overdriveValue.textContent = Math.round(e.overdrive); }
      if (e.tremolo_depth !== undefined) { tremoloDepthSlider.value = Math.round(e.tremolo_depth); tremoloDepthValue.textContent = Math.round(e.tremolo_depth); }
      if (e.tremolo_rate !== undefined) { tremoloRateSlider.value = Math.round(e.tremolo_rate); tremoloRateValue.textContent = Math.round(e.tremolo_rate); }
      if (e.delay_time !== undefined) { delayTimeSlider.value = Math.round(e.delay_time); delayTimeValue.textContent = Math.round(e.delay_time); }
      if (e.delay_feedback !== undefined) { delayFeedbackSlider.value = Math.round(e.delay_feedback); delayFeedbackValue.textContent = Math.round(e.delay_feedback); }
      if (e.reverb_wet !== undefined) { reverbSlider.value = Math.round(e.reverb_wet); reverbValue.textContent = Math.round(e.reverb_wet); }
    }

    // Show MIDI indicator if update came from MIDI
    if (data.source === 'midi') {
      showMidiIndicator();
    }
  });

  socket.on('synth:cc_mapping', (mapping) => {
    console.log('CC Mapping:', mapping);
    ccMapping = mapping;
    updateMappingDisplay();
  });

  socket.on('synth:midi_status', (data) => {
    console.log('MIDI Status:', data);
    midiAvailable = data.available;
    updateMidiStatus(data.device_name);
  });

  socket.on('synth:midi_cc', (data) => {
    // Raw MIDI CC message
    logMidiActivity(`CC${data.control} = ${data.value}`);

    // If in learn mode, capture this CC
    if (currentLearnParam) {
      socket.emit('synth:update_cc_mapping', {
        param: currentLearnParam,
        cc: data.control
      });
      learnBtns.forEach(btn => btn.classList.remove('learning'));
      currentLearnParam = null;
    }
  });

  socket.on('synth:cc_learn', (data) => {
    currentLearnParam = data.param;
    logMidiActivity(`Learning CC for: ${data.param}`);
  });

  // --- UI Event Handlers ---

  // Waveform selector
  waveformBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const waveform = btn.getAttribute('data-waveform');
      socket.emit('synth:set_waveform', { waveform });
      setActiveWaveform(waveform);
    });
  });

  function setActiveWaveform(waveform) {
    waveformBtns.forEach(btn => {
      if (btn.getAttribute('data-waveform') === waveform) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
  }

  function setActiveVoices(n) {
    const voices = String(n);
    voicesBtns.forEach(btn => {
      if (btn.getAttribute('data-voices') === voices) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });
  }

  // Voices selector
  voicesBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const voices = parseInt(btn.getAttribute('data-voices'));
      socket.emit('synth:set_voices', { voices });
      setActiveVoices(voices);
      statusVoices.textContent = voices;
    });
  });

  // Envelope sliders — throttled + final-value guarantee on release
  const _tAttack   = throttle((v) => socket.emit('synth:set_envelope', { attack: v }),   _SLIDER_THROTTLE_MS);
  const _tDecay    = throttle((v) => socket.emit('synth:set_envelope', { decay: v }),    _SLIDER_THROTTLE_MS);
  const _tSustain  = throttle((v) => socket.emit('synth:set_envelope', { sustain: v }),  _SLIDER_THROTTLE_MS);
  const _tRelease  = throttle((v) => socket.emit('synth:set_envelope', { release: v }),  _SLIDER_THROTTLE_MS);
  const _tGlide    = throttle((v) => socket.emit('synth:set_envelope', { glide: v }),    _SLIDER_THROTTLE_MS);

  attackSlider.addEventListener('input',  (e) => { const v = parseInt(e.target.value); attackValue.textContent  = v; _tAttack(v);  });
  attackSlider.addEventListener('change', (e) => socket.emit('synth:set_envelope', { attack:   parseInt(e.target.value) }));

  decaySlider.addEventListener('input',   (e) => { const v = parseInt(e.target.value); decayValue.textContent   = v; _tDecay(v);   });
  decaySlider.addEventListener('change',  (e) => socket.emit('synth:set_envelope', { decay:    parseInt(e.target.value) }));

  sustainSlider.addEventListener('input', (e) => { const v = parseInt(e.target.value); sustainValue.textContent = v; _tSustain(v); });
  sustainSlider.addEventListener('change',(e) => socket.emit('synth:set_envelope', { sustain:  parseInt(e.target.value) }));

  releaseSlider.addEventListener('input', (e) => { const v = parseInt(e.target.value); releaseValue.textContent = v; _tRelease(v); });
  releaseSlider.addEventListener('change',(e) => socket.emit('synth:set_envelope', { release:  parseInt(e.target.value) }));

  glideSlider.addEventListener('input',   (e) => { const v = parseInt(e.target.value); glideValue.textContent   = v; _tGlide(v);   });
  glideSlider.addEventListener('change',  (e) => socket.emit('synth:set_envelope', { glide:    parseInt(e.target.value) }));

  // Effects sliders — throttled + final-value guarantee on release
  const _tCutoff   = throttle((v) => socket.emit('synth:set_effects', { cutoff:          v }), _SLIDER_THROTTLE_MS);
  const _tRes      = throttle((v) => socket.emit('synth:set_effects', { resonance:       v }), _SLIDER_THROTTLE_MS);
  const _tOD       = throttle((v) => socket.emit('synth:set_effects', { overdrive:       v }), _SLIDER_THROTTLE_MS);
  const _tTrD      = throttle((v) => socket.emit('synth:set_effects', { tremolo_depth:   v }), _SLIDER_THROTTLE_MS);
  const _tTrR      = throttle((v) => socket.emit('synth:set_effects', { tremolo_rate:    v }), _SLIDER_THROTTLE_MS);
  const _tDlyT     = throttle((v) => socket.emit('synth:set_effects', { delay_time:      v }), _SLIDER_THROTTLE_MS);
  const _tDlyF     = throttle((v) => socket.emit('synth:set_effects', { delay_feedback:  v }), _SLIDER_THROTTLE_MS);
  const _tRev      = throttle((v) => socket.emit('synth:set_effects', { reverb_wet:      v }), _SLIDER_THROTTLE_MS);

  cutoffSlider.addEventListener('input',        (e) => { const v = parseInt(e.target.value); cutoffValue.textContent        = v; _tCutoff(v);  });
  cutoffSlider.addEventListener('change',       (e) => socket.emit('synth:set_effects', { cutoff:         parseInt(e.target.value) }));

  resonanceSlider.addEventListener('input',     (e) => { const v = parseInt(e.target.value); resonanceValue.textContent     = v; _tRes(v);    });
  resonanceSlider.addEventListener('change',    (e) => socket.emit('synth:set_effects', { resonance:      parseInt(e.target.value) }));

  overdriveSlider.addEventListener('input',     (e) => { const v = parseInt(e.target.value); overdriveValue.textContent     = v; _tOD(v);     });
  overdriveSlider.addEventListener('change',    (e) => socket.emit('synth:set_effects', { overdrive:      parseInt(e.target.value) }));

  tremoloDepthSlider.addEventListener('input',  (e) => { const v = parseInt(e.target.value); tremoloDepthValue.textContent  = v; _tTrD(v);   });
  tremoloDepthSlider.addEventListener('change', (e) => socket.emit('synth:set_effects', { tremolo_depth:  parseInt(e.target.value) }));

  tremoloRateSlider.addEventListener('input',   (e) => { const v = parseInt(e.target.value); tremoloRateValue.textContent   = v; _tTrR(v);   });
  tremoloRateSlider.addEventListener('change',  (e) => socket.emit('synth:set_effects', { tremolo_rate:   parseInt(e.target.value) }));

  delayTimeSlider.addEventListener('input',     (e) => { const v = parseInt(e.target.value); delayTimeValue.textContent     = v; _tDlyT(v);  });
  delayTimeSlider.addEventListener('change',    (e) => socket.emit('synth:set_effects', { delay_time:     parseInt(e.target.value) }));

  delayFeedbackSlider.addEventListener('input', (e) => { const v = parseInt(e.target.value); delayFeedbackValue.textContent = v; _tDlyF(v);  });
  delayFeedbackSlider.addEventListener('change',(e) => socket.emit('synth:set_effects', { delay_feedback: parseInt(e.target.value) }));

  reverbSlider.addEventListener('input',        (e) => { const v = parseInt(e.target.value); reverbValue.textContent        = v; _tRev(v);   });
  reverbSlider.addEventListener('change',       (e) => socket.emit('synth:set_effects', { reverb_wet:     parseInt(e.target.value) }));

  // Frequency slider
  frequencySlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    frequencyValue.textContent = value;
    socket.emit('synth:set_frequency', { frequency: value });
  });

  // Amplitude slider
  amplitudeSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value) / 100.0;
    amplitudeValue.textContent = value.toFixed(2);
    socket.emit('synth:set_amplitude', { amplitude: value });
  });

  // Volume slider
  volumeSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    volumeValue.textContent = value;
    socket.emit('synth:set_volume', { volume: value });
  });

  // Virtual keyboard
  keyboardKeys.forEach(key => {
    key.addEventListener('mousedown', () => {
      const note = parseInt(key.getAttribute('data-note'));
      socket.emit('synth:note_on', { note, velocity: 100 });
      key.classList.add('pressed');
    });

    key.addEventListener('mouseup', () => {
      const note = parseInt(key.getAttribute('data-note'));
      socket.emit('synth:note_off', { note });
      key.classList.remove('pressed');
    });

    key.addEventListener('mouseleave', () => {
      if (key.classList.contains('pressed')) {
        const note = parseInt(key.getAttribute('data-note'));
        socket.emit('synth:note_off', { note });
        key.classList.remove('pressed');
      }
    });
  });

  // MIDI CC Mapping - Learn buttons
  learnBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const param = btn.getAttribute('data-param');
      
      // Clear any existing learn state
      learnBtns.forEach(b => b.classList.remove('learning'));
      
      // Set this button as learning
      btn.classList.add('learning');
      currentLearnParam = param;
      
      socket.emit('synth:learn_cc', { param });
      logMidiActivity(`Waiting for CC for: ${param}...`);
    });
  });

  // MIDI CC Mapping - Clear buttons
  clearBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const param = btn.getAttribute('data-param');
      socket.emit('synth:update_cc_mapping', { param, cc: null });
      logMidiActivity(`Cleared mapping for: ${param}`);
    });
  });

  // --- Helper Functions ---

  function updateMidiStatus(deviceName) {
    const deviceNameElem = document.getElementById('device-name');
    if (midiAvailable) {
      midiStatus.querySelector('.status-dot').classList.add('active');
      midiStatus.querySelector('.status-text').textContent = 'MIDI Connected';
      if (deviceNameElem && deviceName) {
        deviceNameElem.textContent = deviceName;
      }
    } else {
      midiStatus.querySelector('.status-dot').classList.remove('active');
      midiStatus.querySelector('.status-text').textContent = 'No MIDI Device';
      if (deviceNameElem) {
        deviceNameElem.textContent = '';
      }
    }
  }

  function updateMappingDisplay() {
    for (const [param, cc] of Object.entries(ccMapping)) {
      const element = document.getElementById(`map-${param}`);
      if (element) {
        element.textContent = cc !== null ? `CC${cc}` : 'Not mapped';
      }
    }
  }

  function logMidiActivity(message) {
    // Remove placeholder if present
    const placeholder = midiLog.querySelector('.placeholder');
    if (placeholder) {
      placeholder.remove();
    }

    // Add new log entry
    const entry = document.createElement('p');
    entry.textContent = `${new Date().toLocaleTimeString()}: ${message}`;
    midiLog.appendChild(entry);

    // Keep only last 10 entries
    while (midiLog.children.length > 10) {
      midiLog.removeChild(midiLog.firstChild);
    }

    // Auto-scroll to bottom
    midiLog.scrollTop = midiLog.scrollHeight;
  }

  function showMidiIndicator() {
    midiIndicator.classList.add('active');
    
    // Clear existing timeout
    if (midiIndicatorTimeout) {
      clearTimeout(midiIndicatorTimeout);
    }
    
    // Auto-hide after 1 second
    midiIndicatorTimeout = setTimeout(() => {
      midiIndicator.classList.remove('active');
    }, 1000);
  }

  // Initialize
  console.log('Synth Emulator initialized');
})();
