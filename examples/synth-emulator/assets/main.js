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

  // Envelope sliders
  attackSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    attackValue.textContent = value;
    socket.emit('synth:set_envelope', { attack: value });
  });

  decaySlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    decayValue.textContent = value;
    socket.emit('synth:set_envelope', { decay: value });
  });

  sustainSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    sustainValue.textContent = value;
    socket.emit('synth:set_envelope', { sustain: value });
  });

  releaseSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    releaseValue.textContent = value;
    socket.emit('synth:set_envelope', { release: value });
  });

  glideSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    glideValue.textContent = value;
    socket.emit('synth:set_envelope', { glide: value });
  });

  // Effects sliders
  cutoffSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    cutoffValue.textContent = value;
    socket.emit('synth:set_effects', { cutoff: value });
  });

  resonanceSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    resonanceValue.textContent = value;
    socket.emit('synth:set_effects', { resonance: value });
  });

  overdriveSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    overdriveValue.textContent = value;
    socket.emit('synth:set_effects', { overdrive: value });
  });

  tremoloDepthSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    tremoloDepthValue.textContent = value;
    socket.emit('synth:set_effects', { tremolo_depth: value });
  });

  tremoloRateSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    tremoloRateValue.textContent = value;
    socket.emit('synth:set_effects', { tremolo_rate: value });
  });

  delayTimeSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    delayTimeValue.textContent = value;
    socket.emit('synth:set_effects', { delay_time: value });
  });

  delayFeedbackSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    delayFeedbackValue.textContent = value;
    socket.emit('synth:set_effects', { delay_feedback: value });
  });

  reverbSlider.addEventListener('input', (e) => {
    const value = parseInt(e.target.value);
    reverbValue.textContent = value;
    socket.emit('synth:set_effects', { reverb_wet: value });
  });

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
