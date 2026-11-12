# Synth Emulator

The **Synth Emulator** example turns the board into a small polyphonic synthesizer: play it from the web interface or from a USB MIDI keyboard, shape the sound with an ADSR envelope and an effects chain, and map the knobs of your controller to any parameter.

**Note:** On the UNO Q this example requires **Network Mode** or **Single-Board Computer (SBC)** mode, since a **[USB-C® hub](https://store.arduino.cc/products/usb-c-hub-8-in-1)** is needed to connect the audio device and the MIDI keyboard.

_This example is based on the Arduino UNO Q, but also works on the Arduino VENTUNO Q._

The App generates the audio in real time: up to three voices are mixed in software, passed through a resonant low-pass filter, overdrive, tremolo, delay and reverb, and streamed with low latency to the first available speaker, a USB one or the 3.5 mm jack of the UNO Media Carrier. A USB MIDI keyboard is optional and hot-pluggable.

**Key features include:**

- Four waveforms: sine, square, sawtooth and triangle
- 1 to 3 voice polyphony with ADSR envelope and glide (portamento)
- Effects chain: low-pass filter with resonance, overdrive, tremolo, delay and reverb
- USB MIDI input with note velocity, pitch bend and control change messages, detected when plugged in
- Configurable CC mapping with a learn mode, so any knob can drive any parameter
- Virtual keyboard and MIDI activity monitor in the web interface
- Audio on USB speakers, wireless USB audio receivers and the 3.5 mm jack of the UNO Media Carrier

## Bricks Used

- `web_ui`: Brick that provides the web interface and a WebSocket channel for real-time control of the synthesizer.
- `wave_generator`: Brick whose container provides the audio stack. The example ships its own polyphonic generator built on the `Speaker` peripheral, since the Brick plays one voice at a time.

## Hardware Requirements

- Arduino UNO Q (x1) or Arduino VENTUNO Q (x1)
- [USB-C® cable](https://store.arduino.cc/products/usb-c-cable-24-pin) (for power and programming) (x1) _(only for UNO Q)_
- **[USB-C® hub](https://store.arduino.cc/products/usb-c-hub-8-in-1) with external power (x1)** _(only for UNO Q)_
- An **audio device** (choose one):
  - **USB speaker** (cabled)
  - **USB wireless speaker receiver/dongle** (2.4 GHz, non-Bluetooth)
  - **3.5 mm jack speaker** (using [Arduino® UNO™ Media Carrier](https://store.arduino.cc/products/uno-media-carrier))
- A **USB MIDI keyboard or controller** (optional)
- A **power supply** (5 V, 3 A) for the USB hub (e.g. a phone charger) _(only for UNO Q)_

**Important:** A **[USB-C® hub](https://store.arduino.cc/products/usb-c-hub-8-in-1) is mandatory** for this example _(only for UNO Q)_: the UNO Q's single port must be used for the hub, which provides the connections for the power supply, the audio device and the MIDI keyboard, so the example must be run in **[Network Mode](/learn/network-mode)** or **[SBC Mode](/learn/single-board-computer)**.

**Note:** **HDMI audio** and **Bluetooth® Speakers** are not supported by this App.

## How to Use the Example

1. **Hardware Setup**

   Connect the audio device and, if you have one, the MIDI keyboard to the board (on the UNO Q, through the powered USB-C® hub). A USB speaker is preferred over the jack when both are present.

2. **Run the App**

   Launch the App from Arduino App Lab. Wait until the App has launched completely.

3. **Access the Web Interface**

   Open the App in your browser at `<UNO-Q-IP-ADDRESS>:7000` (typically `192.168.x.x`).

4. **Play**

   Click the keys of the virtual keyboard, or play the MIDI keyboard: the status bar shows **MIDI Connected** with the device name when one is detected. The keyboard can be plugged in or removed while the App runs.

5. **Shape the Sound**

   Choose a waveform, set the number of voices and adjust the envelope: attack (0-500 ms), decay, sustain, release (0-1000 ms) and glide (0-200 ms). The effects panel controls filter cutoff and resonance, overdrive, tremolo depth and rate, delay time and feedback, and reverb.

6. **Map Your Controller**

   In the **MIDI CC Mapping** panel click **Learn** next to a parameter and move a knob or fader on the controller: its CC number is assigned to the parameter. **Clear** removes a mapping. The default mapping is:

   | CC                  | Parameter     |
   | ------------------- | ------------- |
   | 1 (modulation)      | Attack        |
   | 2                   | Release       |
   | 3                   | Glide         |
   | 7 (volume)          | Amplitude     |
   | 11 (expression)     | Master volume |

   A CC mapped to **Waveform** selects it from the value: 0-31 sine, 32-63 square, 64-95 sawtooth, 96-127 triangle. The pitch bend wheel bends all playing notes by up to ±2 semitones.

## How it Works

The App is made of three Python modules in `python/`:

- `main.py` wires the web interface and the MIDI keyboard to the generator. A single table (`PARAMS`) describes every parameter: its range when driven by a CC (0-127), its unit in the generator and its unit in the web interface, so the same code serves the sliders, the CC mapping and the state sent to the browser. MIDI callbacks run on the MIDI listener thread: they only touch the generator and queue the messages for the browser, which a separate thread sends, so that the audio thread is never delayed by socket I/O. Knobs and wheels are applied at most every 25 ms.
- `poly_wave_generator.py` is the synthesizer. It is a Brick (`@brick`), so the App starts its audio loop. Each block of 64 samples sums the active voices, each with its own oscillator and ADSR envelope, applies the effects chain and writes it to the `Speaker`. Parameters are smoothed from block to block, so fast changes do not click. With no speaker given it opens the first available one, USB first and then the jack of the media carrier, in exclusive mode for the lowest latency; a pre-configured `Speaker` can be passed in, provided it is mono, 48 kHz, float32.
- `midi_keyboard.py` is a peripheral (`@peripheral`) that reads the raw ALSA MIDI device (`/dev/snd/midiC*D*`) directly, with no extra packages. It parses the MIDI byte stream (running status, system exclusive and real-time messages are handled), delivers note on/off, control change, pitch bend, aftertouch and program change to callbacks, and keeps looking for the device: it is opened when plugged in and reopened after an unplug, reporting the transitions through `on_status_changed`.

## Troubleshooting

- **No audio output**: check that a USB speaker or the media carrier is connected and that the master volume and the amplitude are not at 0. The App log reports which audio output is in use; it stops with an error when none is found.
- **MIDI not detected**: check that the keyboard appears in `ls /dev/snd/midi*` on the board. Some controllers need to be powered on or set to USB MIDI mode.
- **CC mapping not working**: use the learn mode and watch the MIDI activity monitor to see the messages the controller sends. Only control change messages are mapped, not NRPN or SysEx.
