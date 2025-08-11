# Kokoro TTS GUI

A graphical user interface for the Kokoro text-to-speech system.

## Features

- Easy-to-use GUI for converting text files to speech
- Multiple voice options (American Female, British Male, American Male)
- Progress tracking and estimated time remaining
- Support for various audio output formats (WAV, MP3, FLAC)
- Automatic sentence splitting for long texts
- Parallel processing for faster generation

## Installation

```bash
pip install kokoro-tts-gui
```

## Usage

Run the GUI application:

```bash
kokoro-tts-gui
```

Or if you've installed it locally:

```bash
python -m kokoro_tts_gui
```

## Requirements

- Python 3.10 or higher (but less than 3.13)
- kokoro 0.9.4 or higher
- tkinter (usually included with Python)
- nltk
- torch
- soundfile

## How to Use

1. Select a text file to convert to speech
2. Choose an output file path for the audio
3. Select a voice option (American Female, British Male, American Male)
4. Click "Convert to Speech" and wait for the process to complete
5. Your audio file will be saved to the specified location

## License

This project is licensed under the MIT License.