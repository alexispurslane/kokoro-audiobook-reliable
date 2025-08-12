# Agent Guidelines for whisperspeech-tts-gui

## Build/Test Commands
- Syntax checking: `uv run python -m py_compile`
- Type checking: `pyright` (configured in pyproject.toml)
- No linting commands configured - add if needed
- **NEVER run the application yourself** - use `uv run python` for all Python execution

## Code Style Guidelines
- **Imports**: Standard library first, then third-party, then local modules
- **Formatting**: Follow existing 4-space indentation, consistent with Python standards
- **Types**: Use type hints where appropriate, but codebase is not fully typed
- **Naming**: 
  - Classes: PascalCase (e.g., `TextToSpeechApp`, `AppState`)
  - Functions/Methods: snake_case (e.g., `convert_to_speech`, `process_chunk`)
  - Variables: snake_case (e.g., `input_path_var`, `current_chunk_idx`)
- **Error Handling**: Use try-except blocks with specific exception types, include traceback for debugging
- **Threading**: Use `threading.Thread` for background tasks, UI updates via `root.after()`
- **State Management**: Use `AppState` class for atomic state transitions across threads, to communicate state between workers and the main app.
- **UI Architecture**: Text processing, TTS, and workers must NEVER directly update UI or UI state. Always use callback functions provided by TextToSpeechApp to talk to the UI. Directly manipulating `app_state` is, unfortunately, acceptable.
