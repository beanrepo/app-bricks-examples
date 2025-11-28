# Synth Emulator

A MIDI-controllable synthesizer emulator built with Arduino App Bricks. Control synthesis parameters via web interface or MIDI controller with configurable CC mapping.

## Features

- **Multiple Waveforms**: Sine, square, sawtooth, and triangle waves
- **Envelope Control**: Adjustable attack, release, and glide (portamento) parameters
- **MIDI Integration**: Full MIDI keyboard support with note-on/off, pitch bend, and CC messages
- **Configurable CC Mapping**: Map any MIDI CC to synth parameters via the web interface
- **CC Learn Mode**: Click "Learn" to capture the next MIDI CC and assign it to a parameter
- **Virtual Keyboard**: Play notes directly from the web interface
- **Real-time Parameter Control**: Adjust frequency, amplitude, and envelope settings on the fly
- **MIDI Activity Monitor**: View all incoming MIDI messages in real-time

## Requirements

- Arduino UNO Q
- USB speaker/audio output device
- (Optional) USB MIDI keyboard/controller for MIDI input

## Usage

### Basic Operation

1. **Launch the app** and open the web interface
2. **Select a waveform** (sine, square, sawtooth, or triangle)
3. **Adjust envelope parameters**:
   - Attack: How quickly the sound reaches full volume (0-500ms)
   - Release: How quickly the sound fades out (0-1000ms)
   - Glide: Portamento time between notes (0-200ms)
4. **Control the synth**:
   - Use the virtual keyboard to play notes
   - Adjust frequency and amplitude sliders manually
   - Set master volume (0-100%)

### MIDI Controller Setup

If a MIDI device is connected, it will be automatically detected.

#### Default CC Mappings

The synth comes with default MIDI CC mappings:

- **CC1** (Modulation Wheel) → Attack time
- **CC2** → Release time
- **CC3** → Glide time
- **CC7** (Volume) → Amplitude
- **CC11** (Expression) → Master Volume

#### Configuring Custom CC Mappings

1. Navigate to the **MIDI CC Mapping** panel on the right
2. Click **"Learn"** next to the parameter you want to map
3. Move/turn the knob/fader on your MIDI controller
4. The CC number will be captured and assigned automatically
5. Click **"Clear"** to remove a mapping

#### Available Parameters for Mapping

- **Waveform**: Select waveform type (CC value 0-31 = sine, 32-63 = square, 64-95 = sawtooth, 96-127 = triangle)
- **Attack**: Envelope attack time (0-500ms)
- **Release**: Envelope release time (0-1000ms)
- **Glide**: Portamento/glide time (0-200ms)
- **Frequency**: Direct frequency control (100-2000 Hz)
- **Amplitude**: Output amplitude (0.0-1.0)
- **Master Volume**: Hardware volume level (0-100%)

### Playing Notes

#### Via MIDI Keyboard

- Press keys to trigger notes with velocity sensitivity
- Multiple notes can be played (last-note priority)
- Use pitch bend wheel for ±2 semitone modulation
- Use modulation wheel and other CCs to control parameters

#### Via Virtual Keyboard

- Click keys in the web interface to play notes
- Each key triggers a fixed velocity of 100
- Limited to one note at a time

## Architecture

The synth emulator uses these Arduino App Bricks:

- **`wave_generator`**: Continuous audio synthesis engine
  - Generates waveforms in real-time
  - Handles envelope smoothing (attack/release/glide)
  - Manages speaker output and buffering

- **`web_ui`**: Web interface and Socket.IO server
  - Serves the HTML/CSS/JavaScript frontend
  - Handles WebSocket communication for parameter updates
  - Broadcasts state changes to all connected clients

- **`midi_keyboard`** (peripheral): MIDI input handler
  - Direct ALSA raw MIDI device access (no external dependencies)
  - Parses note-on/off, CC, and pitch bend messages
  - Provides callback-based event system

## Code Structure

```
synth-emulator/
├── python/
│   └── main.py          # Backend application logic
├── assets/
│   ├── index.html       # Web UI structure
│   ├── main.js          # Frontend logic and MIDI CC mapping
│   ├── style.css        # UI styling
│   └── libs/            # Socket.IO client library
├── app.yaml             # App configuration
└── README.md            # This file
```

### Backend (main.py)

The backend handles:
- WaveGenerator initialization and state management
- MIDI device detection and callback registration
- CC mapping configuration (stored in memory)
- WebSocket event handlers for UI updates
- Real-time parameter updates from web and MIDI

### Frontend (index.html + main.js)

The frontend provides:
- Waveform selector buttons
- Sliders for envelope, frequency, amplitude, and volume
- Virtual keyboard for note triggering
- MIDI CC mapping interface with learn mode
- Real-time MIDI activity monitor
- Status bar showing current synth state

## Tips

- **Low Latency**: The WaveGenerator is configured with 10ms blocks and optimized buffering for responsive playback
- **Smooth Transitions**: Envelope parameters ensure smooth frequency and amplitude changes
- **MIDI Learn**: Use learn mode to quickly set up your MIDI controller without looking up CC numbers
- **Multiple Clients**: Open the web interface on multiple devices - all will stay synchronized
- **Velocity Sensitivity**: MIDI keyboard velocity controls note amplitude (web keyboard uses fixed velocity)
- **Pitch Bend Range**: ±2 semitones for expressive playing

## Troubleshooting

### No audio output
- Check that a USB speaker is connected and detected
- Verify master volume is not at 0%
- Check amplitude slider is not at 0

### MIDI not detected
- Ensure USB MIDI device is connected before starting the app
- Check `/dev/snd/midiC*D*` devices exist: `ls /dev/snd/midi*`
- Verify ALSA MIDI devices: `aplaymidi -l`

### CC mapping not working
- Use the "Learn" button to capture CC messages
- Check the MIDI activity monitor to verify CC messages are received
- Ensure your MIDI controller is sending CC messages (not NRPN or other types)
