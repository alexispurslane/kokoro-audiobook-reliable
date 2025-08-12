# Kokoro TTS GUI

A graphical user interface for the Kokoro text-to-speech system with advanced features for processing long texts.

## Features

- **Fast and high quality** text to speech conversion, thanks to Kokoro-82M
- **Easy-to-use cross-platform native GUI** for converting text files to high-quality speech
- **28 different voice options** including American, British, and other accents for both male and female voices
- **Progress tracking** with real-time status updates and estimated time remaining
- **Support for various audio output formats** (WAV, MP3, FLAC)
- **Advanced text processing** with automatic sentence splitting and long sentence handling
- **Exception-proof resume capability** - never lose progress on a long running project!
- **Pause and Stop functionality** - pause conversions to resume later or stop without saving progress, based on the same resume capability features
- **Real-time console output** showing detailed processing information

## Unique Features

The reason I made this app instead of using one of the many, many existing options, was that they all lacked two key features that my version has:

### Resume capability
This automatically saves progress as a fully usable audio file as well as a lock file representing where the conversion left off and what settings it was using, so that it can resume interrupted conversions when you quit the application, send SIGINT or SIGTERM, or even if it runs into exceptions ---

### Long sentence handling
Kokoro-82M text-to-speech degrades after ~250 characters; this causes problems for existing Kokoro TTS solutions, which split by sentence, but don't actually check to make sure sentences are small enough for it to process before feeding them into the model: long sentences can drop words or become whispers at the end. I implemented a recursive algorithm that first splits text at natural syntactic boundaries (semicolons, colons, emdashes, commas, conjunctions), then falls back to greedy word fitting if needed, to ensure that all chunks fed to Kokoro-82M are always below the character limit. After generating audio for each chunk, I then reassemble them with precisely trimmed silence, insert sentence pauses, and add double pauses at paragraph breaks, resulting in clear, accurate audio.

## Installation

1. Clone the repository:
   ```bash
   git clone <this repo>
   cd <this repo>
   ```

2. Install dependencies using uv:
   ```bash
   uv sync
   ```

## Usage

Run the GUI application:
```bash
uv run main.py
```

## Requirements

- Python 3.10 or higher (but less than 3.13)
- uv (for dependency management)
- kokoro 0.9.4 or higher
- tkinter (usually included with Python)
- nltk
- torch
- soundfile
- ffmpeg<=0.6 (can automatically find and use this on Mac if it's installed with Homebrew)

## How to Use

1. Select a text file to convert to speech
2. Choose an output file path for the audio
3. Select a voice option from the 28 available voices
4. Adjust silence trimming parameters if desired:
   - **Silence Trim Threshold**: Controls sensitivity of silence detection (0-0.5)
   - **Silence Trim Margin**: Extra samples to keep before/after detected sound (0-500ms)
5. Click "Convert to Speech" to start the conversion
6. Monitor progress in the progress bar and console output
7. Use "Pause" to interrupt and save progress for later resumption, or "Stop" to end without saving progress
8. Your audio file will be saved to the specified location

## Note

This project was written as an experiment with agentic AI coding, using OpenCode with GLM 4.5 and Qwen 3 Coder. All diffs were carefully reviewed, all algorithms, features, and the logic of implementing those features were described to the AI by me (although it did fill in the blanks here and there), and I've asked it to, or manually myself, refactored and cleaned the code up multiple times. Nevertheless, this was a somewhat vibe-coded project, more than I'd usually be comfortable with if that wasn't the purpose of this experiment: I'm intimately familiar with what the code does on a specific level, how it does it, and why, and what functions exist and how they relate to each other, but I am less familiar with the spatial layout of the codebase, and since this is a personal script and not a long term enterprises project, I've sort of not worried about really factoring out the architecture much.
