import os
import sys
import json
import atexit

# Check if we're already running with correct FFmpeg path and restart if needed
ffmpeg_lib_path = '/opt/homebrew/opt/ffmpeg@6/lib'
current_dyld_path = os.environ.get('DYLD_LIBRARY_PATH', '')

if ffmpeg_lib_path not in current_dyld_path:
    # Restart script with correct environment
    new_env = os.environ.copy()
    new_env['DYLD_LIBRARY_PATH'] = f"{ffmpeg_lib_path}:{current_dyld_path}" if current_dyld_path else ffmpeg_lib_path
    
    # Restart the script with correct environment
    os.execvpe(sys.executable, [sys.executable] + sys.argv, new_env)

# Suppress torch warnings before importing any modules that might import torch
import warnings
warnings.filterwarnings("ignore", module="torch")
warnings.filterwarnings("ignore", category=UserWarning, module="torch")
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")

import time
import signal
import tempfile
import simpleaudio as sa
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Try to import tkinterdnd2
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    TKDND_AVAILABLE = True
except ImportError:
    TKDND_AVAILABLE = False
    print("tkinterdnd2-universal not available, drag-and-drop support will be limited")

import threading
import numpy as np
import soundfile as sf
from typing import Optional

# Import our new modules
from tts_generator import process_chunk, generate_long
from queue_worker import QueueWorker
from convert_worker import ConvertWorker
from text_processor import clean_unicode_text

class ToolTip:
    """A tooltip class for tkinter widgets based on the GeeksforGeeks approach"""
    
    def __init__(self, widget, text, delay=1000):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self.id = None
        self.x = self.y = 0
        
        # Bind events
        self.widget.bind("<Enter>", self.on_enter, add="+")
        self.widget.bind("<Leave>", self.on_leave, add="+")
        self.widget.bind("<ButtonPress>", self.on_leave, add="+")
        
    def on_enter(self, event=None):
        """Handle mouse enter event"""
        self.schedule()
        
    def on_leave(self, event=None):
        """Handle mouse leave event"""
        self.unschedule()
        self.hide_tooltip()
        
    def schedule(self):
        """Schedule tooltip to appear after delay"""
        self.unschedule()
        self.id = self.widget.after(self.delay, self.show_tooltip)
        
    def unschedule(self):
        """Cancel scheduled tooltip"""
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
            
    def show_tooltip(self):
        """Display the tooltip"""
        # Don't show tooltip if there's no text
        if not self.text:
            return
            
        # Get mouse position
        x, y = self.widget.winfo_pointerxy()
        
        # Create tooltip window
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        
        # Position tooltip near mouse cursor
        tw.wm_geometry(f"+{x + 10}+{y + 10}")
        
        # Create tooltip label
        label = tk.Label(
            tw, 
            text=self.text,
        )
        label.pack(ipadx=1)
        
    def hide_tooltip(self):
        """Hide the tooltip"""
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class DnDEntry(ttk.Entry):
    """A tkinter Entry widget with drag-and-drop support"""
    
    def __init__(self, parent, app, *args, **kwargs):
        # Extract our custom arguments
        self.app = app
        
        # Pass the rest to the parent class
        super().__init__(parent, *args, **kwargs)
        
        # Enable drag-and-drop if available
        if TKDND_AVAILABLE:
            self.drop_target_register(DND_FILES)
            self.dnd_bind('<<Drop>>', self.on_drop)
            
    def on_drop(self, event):
        """Handle file drop event"""
        # event.data contains the dropped file paths
        data = event.data
        if data:
            # Handle multiple files if needed, but we only care about the first one
            # On Windows, paths might be enclosed in curly braces
            # On Unix-like systems, paths are separated by spaces
            # But paths with spaces might be quoted
            
            # Try to parse the data as a list of file paths
            try:
                import shlex
                file_paths = shlex.split(data)
            except:
                # Fallback: treat as a single file path
                file_paths = [data]
                
            if file_paths:
                file_path = file_paths[0]  # Take the first file
                if os.path.isfile(file_path) and file_path.endswith('.txt'):
                    print(f"Dropped input file: {file_path}")
                    self.app.input_path_var.set(file_path)
                    
                    # Auto-set output path if not already set
                    if not self.app.output_path_var.get():
                        base_name = os.path.splitext(os.path.basename(file_path))[0]
                        extension = ".mp3" if self.app.convert_to_mp3_var.get() else ".wav"
                        output_path = os.path.join(os.path.dirname(file_path), f"{base_name}{extension}")
                        self.app.output_path_var.set(output_path)
                        
                    self.app._pull_resume_info()
                    self.app.update_recent_files(file_path)
                else:
                    messagebox.showerror("Error", "Please drop a valid text file (.txt)")
class AppState:
    """Atomic application state manager with automatic worker waiting"""
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"
    STOP = "STOP"
    
    def __init__(self, initial_state=None, wait_for_worker_callback=None):
        self._state = initial_state or self.IDLE
        self._wait_for_worker_callback = wait_for_worker_callback
        self._main_thread_id = threading.get_ident()
    
    def get_state(self):
        """Get current state atomically"""
        return self._state
    
    def set_state(self, new_state):
        """Set new state atomically, with automatic worker waiting on main thread"""
        old_state = self._state
        self._state = new_state
        
        # If transitioning to ERROR or STOP on main thread, call wait_for_worker
        if (new_state in (self.ERROR, self.STOP) and 
            old_state not in (self.ERROR, self.STOP) and
            self._wait_for_worker_callback is not None and
            threading.get_ident() == self._main_thread_id):
            print(f"State changed to {new_state} on main thread, waiting for worker...")
            self._wait_for_worker_callback()
    
    @property
    def state(self):
        """Property access to current state"""
        return self._state
    
    @state.setter
    def state(self, new_state):
        """Property setter for state"""
        self.set_state(new_state)
    
    @property
    def is_active(self):
        """True if state represents an active conversion process"""
        return self._state == self.PROCESSING
    
    @property
    def should_create_lockfile(self):
        """True if state should create a lockfile on exit"""
        return self._state in (self.PROCESSING, self.ERROR)
    
    @property
    def is_aborted(self):
        """True if state represents an aborted process"""
        return self._state in (self.ERROR, self.STOP)


class TextToSpeechApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Kokoro TTS Converter")
        # Remove fixed geometry to allow window to fit contents
        # self.root.geometry("600x500")
        
        # Make window resizeable
        self.root.resizable(True, True)
        
        # Initialize pipeline loading state
        self.pipeline_loaded = False
        
        # Initialize workers (will be updated after pipeline loading)
        self.convert_worker = None
        self.queue_worker = None
        
        # For cleanup handling
        self.current_soundfile = None
        self.current_chunk_idx = None
        self.start_time = None

        # For Resume handling
        self.start_chunk_idx = 0
        self.sf_mode = 'w'
        
        # Track conversion state with atomic state management
        self.convert_worker_thread = None
        self.app_state = AppState(wait_for_worker_callback=self._wait_for_worker)
        
        # Text editor state
        self.text_editor_modified = False
        
        # Queue management
        self.queue_items = []  # List of dictionaries with input_file, output_file, status
        self.current_queue_index = -1  # Index of currently processing queue item
        
        # Recent files and settings
        self.config_dir = os.path.expanduser("~/.config/kokoro-tts-gui")
        self.recent_files = []
        
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Create UI elements
        self.create_widgets()
        
        # Load settings after creating widgets
        self.load_settings()
        
        self.root.update()
        self.root.lift()
        
        # No initialization needed for playsound
        
        # Load pipeline in background
        self.load_pipeline()
        
        # Register exit handler to save settings
        atexit.register(self.save_settings)
        
        # Update UI with loaded settings
        self.root.after(100, self.update_ui_with_loaded_settings)
        
    def create_widgets(self):
        # Main container frame
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill="both", expand=True)
        
        # Configure root window weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Create each section using dedicated functions
        self.create_file_settings_section(main_container)
        self.create_queue_section(main_container)
        
        # Create a frame to hold voice settings and audio processing side by side
        settings_container = ttk.Frame(main_container)
        settings_container.pack(fill="x", pady=(0, 10))
        
        # Configure columns to expand equally
        settings_container.columnconfigure(0, weight=1)
        settings_container.columnconfigure(1, weight=1)
        
        # Create voice settings and audio processing sections side by side
        self.create_voice_settings_section(settings_container)
        self.create_audio_processing_settings_section(settings_container)
        
        self.create_progress_section(main_container)
        self.create_control_buttons_section(main_container)
        self.create_console_output_section(main_container)
        
        # Redirect STDOUT to the console text box
        self.original_stdout = sys.stdout
        sys.stdout = self.ConsoleRedirector(self.console_text, self.original_stdout)
        
    def create_file_settings_section(self, parent):
        """Create the file settings section with input/output file selection"""
        # File selection section
        file_frame = ttk.LabelFrame(parent, text="File Settings", padding="10")
        file_frame.pack(fill="x", pady=(0, 10))
        
        # Input file section
        input_frame = ttk.Frame(file_frame)
        input_frame.pack(fill="x", pady=(0, 5))
        
        ttk.Label(input_frame, text="Input Text File:").pack(anchor="w")
        
        input_row_frame = ttk.Frame(input_frame)
        input_row_frame.pack(fill="x", pady=(5, 0))
        input_row_frame.columnconfigure(0, weight=1)
        
        self.input_path_var = tk.StringVar()
        self.input_entry = DnDEntry(input_row_frame, self, textvariable=self.input_path_var, state="readonly")
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ToolTip(self.input_entry, "The text file to convert to speech. Must be a plain text file. The file will be processed in sentence chunks for long-form reliability. You can also drag and drop a text file here.")
        
        # Create a frame for the browse and recent buttons
        button_frame = ttk.Frame(input_row_frame)
        button_frame.pack(side="left")
        
        self.browse_input_btn = ttk.Button(button_frame, text="Browse", command=self.browse_input_file)
        self.browse_input_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.browse_input_btn, "Select a text file to convert to speech.")
        
        # Recent files dropdown menu
        self.recent_menu = tk.Menu(button_frame, tearoff=0)
        self.recent_btn = ttk.Menubutton(button_frame, text="Recent", menu=self.recent_menu)
        self.recent_btn.pack(side="left")
        ToolTip(self.recent_btn, "Open a recently used text file.")
        
        # Update the recent files menu
        self.update_recent_files_menu()
        
        # Collapsible text editor section
        self.editor_collapsed = True
        self.editor_frame = ttk.Frame(file_frame)
        self.editor_frame.pack(fill="x", pady=(10, 0))
        
        # Editor toggle button
        self.editor_toggle_btn = ttk.Button(
            self.editor_frame, 
            text="▼ Edit Text Content", 
            command=self.toggle_text_editor
        )
        self.editor_toggle_btn.pack(anchor="w")
        ToolTip(self.editor_toggle_btn, "Toggle the text editor to view and edit the content that will be processed.")
        
        # Editor content frame (initially hidden)
        self.editor_content_frame = ttk.Frame(self.editor_frame)
        self.editor_content_frame.pack(fill="both", expand=True, pady=(5, 0))
        self.editor_content_frame.pack_forget()  # Hide initially
        
        # Text editor with scrollbar
        editor_text_container = ttk.Frame(self.editor_content_frame)
        editor_text_container.pack(fill="both", expand=True)
        
        self.editor_text = tk.Text(editor_text_container, height=10, wrap="word")
        editor_scrollbar = ttk.Scrollbar(editor_text_container, orient="vertical", command=self.editor_text.yview)
        self.editor_text.configure(yscrollcommand=editor_scrollbar.set)
        
        self.editor_text.pack(side="left", fill="both", expand=True)
        editor_scrollbar.pack(side="right", fill="y")
        ToolTip(self.editor_text, "Edit the text content that will be processed by the TTS engine. Changes here will be used for conversion, even if not saved to the file. Use Ctrl+S or the Save button to save changes to the original file.")
        
        # Editor controls
        editor_controls_frame = ttk.Frame(self.editor_content_frame)
        editor_controls_frame.pack(fill="x", pady=(5, 0))
        
        # Checkbox for replacing single newlines with spaces
        self.replace_newlines_var = tk.BooleanVar(value=False)
        self.replace_newlines_checkbox = ttk.Checkbutton(
            editor_controls_frame, 
            text="Replace single newlines with spaces (preserves double newlines)", 
            variable=self.replace_newlines_var,
            command=self.toggle_newline_replacement
        )
        self.replace_newlines_checkbox.pack(side="left", padx=(0, 10))
        ToolTip(self.replace_newlines_checkbox, "Replace single newlines with spaces while preserving double newlines. Useful for cleaning up text formatting.")
        
        self.save_editor_btn = ttk.Button(editor_controls_frame, text="Save to File", command=self.save_editor_content)
        self.save_editor_btn.pack(side="left")
        ToolTip(self.save_editor_btn, "Save the edited content back to the original file.")
        
        # Bind Ctrl+S to save
        self.editor_text.bind('<Control-s>', lambda event: self.save_editor_content())
        self.editor_text.bind('<Command-s>', lambda event: self.save_editor_content())  # For Mac
        
        # Bind events to track changes
        self.editor_text.bind('<KeyPress>', self.on_text_editor_change)
        self.editor_text.bind('<Button-1>', self.on_text_editor_change)
        
        # Store original text for undoing newline replacement
        self.original_text_content = ""
        
        # Status label for editor
        self.editor_status_var = tk.StringVar(value="")
        self.editor_status_label = ttk.Label(self.editor_content_frame, textvariable=self.editor_status_var, foreground="gray")
        self.editor_status_label.pack(anchor="w", pady=(5, 0))
        
        # Output file section
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill="x", pady=(10, 0))
        
        ttk.Label(output_frame, text="Output Audio File:").pack(anchor="w")
        
        output_row_frame = ttk.Frame(output_frame)
        output_row_frame.pack(fill="x", pady=(5, 0))
        output_row_frame.columnconfigure(0, weight=1)
        
        self.output_path_var = tk.StringVar()
        self.output_entry = ttk.Entry(output_row_frame, textvariable=self.output_path_var)
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ToolTip(self.output_entry, "The output audio file. Will be created in WAV or MP3 format. For MP3 output, an intermediate WAV file is first created to enable partial output and resuming.")
        
        self.browse_output_btn = ttk.Button(output_row_frame, text="Browse", command=self.browse_output_file)
        self.browse_output_btn.pack(side="left")
        ToolTip(self.browse_output_btn, "Select where to save the output audio file.")
        
    def create_queue_section(self, parent):
        """Create the queue section with table view and control buttons"""
        # Queue section
        queue_frame = ttk.LabelFrame(parent, text="Queue", padding="10")
        queue_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Queue control buttons
        queue_control_frame = ttk.Frame(queue_frame)
        queue_control_frame.pack(fill="x", pady=(0, 10))
        
        self.add_to_queue_btn = ttk.Button(queue_control_frame, text="Add to Queue", command=self.add_to_queue)
        self.add_to_queue_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.add_to_queue_btn, "Add the current input/output file pair to the conversion queue. Items will be processed sequentially, one at a time.")
        
        self.clear_queue_btn = ttk.Button(queue_control_frame, text="Clear Queue", command=self.clear_queue)
        self.clear_queue_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.clear_queue_btn, "Remove all items from the conversion queue.")
        
        self.delete_selected_btn = ttk.Button(queue_control_frame, text="Delete Selected", command=self.delete_selected_queue_item)
        self.delete_selected_btn.pack(side="left")
        ToolTip(self.delete_selected_btn, "Remove the selected item from the conversion queue.")
        
        # Queue table
        queue_table_container = ttk.Frame(queue_frame)
        queue_table_container.pack(fill="both", expand=True)
        
        # Create treeview for queue items
        columns = ("input_file", "output_file", "status")
        self.queue_tree = ttk.Treeview(queue_table_container, columns=columns, show="headings", height=6)
        
        # Define column headings and widths
        self.queue_tree.heading("input_file", text="Input File")
        self.queue_tree.heading("output_file", text="Output File")
        self.queue_tree.heading("status", text="Status")
        
        self.queue_tree.column("input_file", width=200)
        self.queue_tree.column("output_file", width=200)
        self.queue_tree.column("status", width=100)
        
        # Add scrollbar
        queue_scrollbar = ttk.Scrollbar(queue_table_container, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=queue_scrollbar.set)
        
        self.queue_tree.pack(side="left", fill="both", expand=True)
        ToolTip(self.queue_tree, "Queue of files to be converted. Shows input file, output file, and conversion status. Processing happens sequentially with automatic resuming on failure.")
        
        queue_scrollbar.pack(side="right", fill="y")
        
    def create_voice_settings_section(self, parent):
        """Create the voice settings section with voice selection dropdown"""
        # Voice selection section
        voice_frame = ttk.LabelFrame(parent, text="Voice Settings", padding="10")
        voice_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # Voice selection
        ttk.Label(voice_frame, text="Voice:").pack(anchor="w")
        
        self.voice_var = tk.StringVar(value="af_heart")
        
        # Voice data with grades and language codes
        self.voice_data = {
            # American English
            "af_heart": {"grade": "A", "language": "American English", "lang_code": "a"},
            "af_alloy": {"grade": "C", "language": "American English", "lang_code": "a"},
            "af_aoede": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "af_bella": {"grade": "A-", "language": "American English", "lang_code": "a"},
            "af_jessica": {"grade": "D", "language": "American English", "lang_code": "a"},
            "af_kore": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "af_nicole": {"grade": "B-", "language": "American English", "lang_code": "a"},
            "af_nova": {"grade": "C", "language": "American English", "lang_code": "a"},
            "af_river": {"grade": "D", "language": "American English", "lang_code": "a"},
            "af_sarah": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "af_sky": {"grade": "C-", "language": "American English", "lang_code": "a"},
            "am_adam": {"grade": "F+", "language": "American English", "lang_code": "a"},
            "am_echo": {"grade": "D", "language": "American English", "lang_code": "a"},
            "am_eric": {"grade": "D", "language": "American English", "lang_code": "a"},
            "am_fenrir": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "am_liam": {"grade": "D", "language": "American English", "lang_code": "a"},
            "am_michael": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "am_onyx": {"grade": "D", "language": "American English", "lang_code": "a"},
            "am_puck": {"grade": "C+", "language": "American English", "lang_code": "a"},
            "am_santa": {"grade": "D-", "language": "American English", "lang_code": "a"},
            
            # British English
            "bf_alice": {"grade": "D", "language": "British English", "lang_code": "b"},
            "bf_emma": {"grade": "B-", "language": "British English", "lang_code": "b"},
            "bf_isabella": {"grade": "C", "language": "British English", "lang_code": "b"},
            "bf_lily": {"grade": "D", "language": "British English", "lang_code": "b"},
            "bm_daniel": {"grade": "D", "language": "British English", "lang_code": "b"},
            "bm_fable": {"grade": "C", "language": "British English", "lang_code": "b"},
            "bm_george": {"grade": "C", "language": "British English", "lang_code": "b"},
            "bm_lewis": {"grade": "D+", "language": "British English", "lang_code": "b"},
            
            # Japanese
            "jf_alpha": {"grade": "C+", "language": "Japanese", "lang_code": "j"},
            "jf_gongitsune": {"grade": "C", "language": "Japanese", "lang_code": "j"},
            "jf_nezumi": {"grade": "C-", "language": "Japanese", "lang_code": "j"},
            "jf_tebukuro": {"grade": "C", "language": "Japanese", "lang_code": "j"},
            "jm_kumo": {"grade": "C-", "language": "Japanese", "lang_code": "j"},
            
            # Mandarin Chinese
            "zf_xiaobei": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zf_xiaoni": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zf_xiaoxiao": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zf_xiaoyi": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zm_yunjian": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zm_yunxi": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zm_yunxia": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            "zm_yunyang": {"grade": "D", "language": "Mandarin Chinese", "lang_code": "z"},
            
            # Spanish
            "ef_dora": {"grade": "N/A", "language": "Spanish", "lang_code": "e"},
            "em_alex": {"grade": "N/A", "language": "Spanish", "lang_code": "e"},
            "em_santa": {"grade": "N/A", "language": "Spanish", "lang_code": "e"},
            
            # French
            "ff_siwis": {"grade": "B-", "language": "French", "lang_code": "f"},
            
            # Hindi
            "hf_alpha": {"grade": "C", "language": "Hindi", "lang_code": "h"},
            "hf_beta": {"grade": "C", "language": "Hindi", "lang_code": "h"},
            "hm_omega": {"grade": "C", "language": "Hindi", "lang_code": "h"},
            "hm_psi": {"grade": "C", "language": "Hindi", "lang_code": "h"},
            
            # Italian
            "if_sara": {"grade": "C", "language": "Italian", "lang_code": "i"},
            "im_nicola": {"grade": "C", "language": "Italian", "lang_code": "i"},
            
            # Brazilian Portuguese
            "pf_dora": {"grade": "N/A", "language": "Brazilian Portuguese", "lang_code": "p"},
            "pm_alex": {"grade": "N/A", "language": "Brazilian Portuguese", "lang_code": "p"},
            "pm_santa": {"grade": "N/A", "language": "Brazilian Portuguese", "lang_code": "p"}
        }
        
        # Create voice list with grades for display
        voices_with_grades = [
            f"{voice} ({data['grade']})" for voice, data in self.voice_data.items()
        ]
        
        # Language mapping
        self.language_codes = {
            "American English": "a",
            "British English": "b",
            "Japanese": "j",
            "Mandarin Chinese": "z",
            "Spanish": "e",
            "French": "f",
            "Hindi": "h",
            "Italian": "i",
            "Brazilian Portuguese": "p"
        }
        
        # Current language (default to American English)
        self.language_var = tk.StringVar(value="American English")
        
        # Create a frame for language dropdown
        language_frame = ttk.Frame(voice_frame)
        language_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(language_frame, text="Language:").pack(anchor="w")
        
        # Language dropdown
        languages = list(self.language_codes.keys())
        self.language_dropdown = ttk.Combobox(language_frame, textvariable=self.language_var, values=languages, state="readonly", width=30)
        self.language_dropdown.pack(side="left", padx=(0, 5))
        self.language_dropdown.set("American English")  # Set default value
        ToolTip(self.language_dropdown, "Select the language for the voice. This will filter the available voices.")
        
        # Bind language change event
        self.language_var.trace_add('write', self._on_language_changed)
        
        # Create a frame for voice dropdown and play sample button
        voice_control_frame = ttk.Frame(voice_frame)
        voice_control_frame.pack(fill="x", pady=(5, 0))
        
        # Voice dropdown with grades
        voices_with_grades = [
            f"{voice} ({data['grade']})" for voice, data in self.voice_data.items()
        ]
        self.voice_dropdown = ttk.Combobox(voice_control_frame, textvariable=self.voice_var, values=voices_with_grades, state="readonly", width=30)
        self.voice_dropdown.pack(side="left", padx=(0, 5))
        self.voice_dropdown.set("af_heart (A)")  # Set default value
        ToolTip(self.voice_dropdown, "Select a voice for the text-to-speech conversion. Voices are rated by quality (A is best). The Kokoro-82M model generates high-quality speech using neural networks.")
        
        # Play Sample button (disabled initially until pipeline loads)
        self.play_sample_btn = ttk.Button(voice_control_frame, text="Play Sample", command=self.play_sample, state="disabled")
        self.play_sample_btn.pack(side="left")
        ToolTip(self.play_sample_btn, "Play a sample of the selected voice with current settings. Uses the Kokoro pipeline to generate and play a short test phrase.")
        
        # Voice speed slider
        ttk.Label(voice_frame, text="Voice Speed:").pack(anchor="w", pady=(10, 0))
        
        speed_container = ttk.Frame(voice_frame)
        speed_container.pack(fill="x", pady=(5, 0))
        
        self.speed_var = tk.DoubleVar(value=1.0)  # Default speed (1.0 = normal)
        self.speed_slider = ttk.Scale(speed_container, from_=0.5, to=2.0, variable=self.speed_var, orient="horizontal")
        self.speed_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ToolTip(self.speed_slider, "Adjust the speed of the voice. 1.0 is normal speed, lower is slower, higher is faster. Speed is controlled directly by the Kokoro model for natural prosody.")
        
        self.speed_value_label = ttk.Label(speed_container, text=f"{self.speed_var.get():.1f}x")
        self.speed_value_label.pack(side="left")
        
        # Update speed value display when slider moves
        self.speed_slider.configure(command=self.update_speed_display)
        
        # Sample rate spinner
        ttk.Label(voice_frame, text="Sample Rate (Hz):").pack(anchor="w", pady=(10, 0))
        
        sample_rate_frame = ttk.Frame(voice_frame)
        sample_rate_frame.pack(fill="x", pady=(5, 0))
        
        self.sample_rate_var = tk.IntVar(value=24000)  # Default sample rate
        self.sample_rate_spinbox = ttk.Spinbox(sample_rate_frame, from_=8000, to=48000, increment=1000, textvariable=self.sample_rate_var, width=10)
        self.sample_rate_spinbox.pack(side="left")
        ToolTip(self.sample_rate_spinbox, "Set the audio sample rate in Hertz. Higher values provide better quality but larger files. Audio is resampled from Kokoro's native 24kHz using torchaudio.")
        
        ttk.Label(sample_rate_frame, text="Hz").pack(side="left", padx=(5, 0))
        
    def create_audio_processing_settings_section(self, parent):
        """Create the audio processing settings section with threshold slider and margin spinbox"""
        # Audio processing settings section
        settings_frame = ttk.LabelFrame(parent, text="Audio Processing Settings", padding="10")
        settings_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # Leading silence threshold slider
        ttk.Label(settings_frame, text="Leading/Trailing Silence Trim Threshold:").pack(anchor="w")
        
        threshold_container = ttk.Frame(settings_frame)
        threshold_container.pack(fill="x", pady=(5, 0))
        
        self.threshold_var = tk.DoubleVar(value=0.06)  # Default threshold value
        # Create a scale with 0.02 increments
        self.threshold_slider = ttk.Scale(threshold_container, from_=0, to=0.5, variable=self.threshold_var, orient="horizontal")
        self.threshold_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ToolTip(self.threshold_slider, "Set the sensitivity for detecting silence. Lower values trim more aggressively. Applied to each audio chunk during generation to reduce awkward pauses. Chunks are calculated by splitting text into sentences and long sentences into sub-chunks.")
        
        self.threshold_value_label = ttk.Label(threshold_container, text=f"{self.threshold_var.get():.2f}")
        self.threshold_value_label.pack(side="left")
        
        # Update threshold value display when slider moves
        self.threshold_slider.configure(command=self.update_threshold_display)
        
        # Margin for silence trimmer
        ttk.Label(settings_frame, text="Silence Trim Margin (ms):").pack(anchor="w", pady=(10, 0))
        
        margin_frame = ttk.Frame(settings_frame)
        margin_frame.pack(fill="x", pady=(5, 0))
        
        self.margin_var = tk.IntVar(value=25)  # Default margin value in ms
        self.margin_spinbox = ttk.Spinbox(margin_frame, from_=0, to=500, textvariable=self.margin_var, width=10)
        self.margin_spinbox.pack(side="left")
        ToolTip(self.margin_spinbox, "Base time in milliseconds to keep before and after detected sound. Converted to samples based on current sample rate and applied during silence trimming of each audio chunk. Multiplied by 2 before the end of a silence, and by 10 after, because human voices trail off more than they trail in.")
        
        ttk.Label(margin_frame, text="ms").pack(side="left", padx=(5, 0))
        
        # Number of parallel batches
        ttk.Label(settings_frame, text="Parallel Batches:").pack(anchor="w", pady=(10, 0))
        
        batch_frame = ttk.Frame(settings_frame)
        batch_frame.pack(fill="x", pady=(5, 0))
        
        # Get the number of logical CPUs
        import multiprocessing
        max_batches = multiprocessing.cpu_count()
        
        self.batch_count_var = tk.IntVar(value=1)  # Default to 1 batch (no parallelism)
        self.batch_count_spinbox = ttk.Spinbox(batch_frame, from_=1, to=max_batches, textvariable=self.batch_count_var, width=10)
        self.batch_count_spinbox.pack(side="left")
        ToolTip(self.batch_count_spinbox, "Number of text chunks to process simultaneously. More batches = faster conversion but higher CPU usage. Each batch uses its own Kokoro pipeline instance.")
        
        ttk.Label(batch_frame, text=f"(1-{max_batches})").pack(side="left", padx=(5, 0))
        
        # Add trace to handle batch count changes
        self.batch_count_var.trace_add('write', self._on_batch_count_changed)
        
        # MP3 conversion checkbox and bitrate
        ttk.Label(settings_frame, text="MP3 Conversion:").pack(anchor="w", pady=(10, 0))
        
        mp3_frame = ttk.Frame(settings_frame)
        mp3_frame.pack(fill="x", pady=(5, 0))
        
        self.convert_to_mp3_var = tk.BooleanVar(value=False)
        self.mp3_checkbox = ttk.Checkbutton(mp3_frame, text="Convert to MP3", variable=self.convert_to_mp3_var)
        self.mp3_checkbox.pack(side="left", padx=(0, 10))
        ToolTip(self.mp3_checkbox, "Convert the output to MP3 format instead of WAV. An intermediate WAV file is first created to allow partial output and resuming, then converted to MP3 using pydub.")
        
        ttk.Label(mp3_frame, text="Bitrate:").pack(side="left", padx=(0, 5))
        
        self.mp3_bitrate_var = tk.StringVar(value="192k")
        mp3_bitrates = ["64k", "96k", "128k", "192k", "256k", "320k"]
        self.mp3_bitrate_combo = ttk.Combobox(mp3_frame, textvariable=self.mp3_bitrate_var, values=mp3_bitrates, state="readonly", width=8)
        self.mp3_bitrate_combo.pack(side="left")
        ToolTip(self.mp3_bitrate_combo, "Select the MP3 bitrate. Higher bitrates provide better quality but larger files. The WAV file is converted to MP3 using pydub's AudioSegment.export method.")
        
        self.mp3_bitrate_combo.set("192k")  # Set default value
        
        # Add callback to update output file extension when MP3 checkbox is toggled
        self.convert_to_mp3_var.trace_add('write', self._on_mp3_checkbox_changed)
        
    def create_progress_section(self, parent):
        """Create the progress section with status label, timer, and progress bar"""
        # Progress section
        progress_frame = ttk.LabelFrame(parent, text="Progress", padding="10")
        progress_frame.pack(fill="x", pady=(0, 10))
        
        # Status label
        self.status_var = tk.StringVar(value="Ready to convert")
        self.status_label = ttk.Label(progress_frame, textvariable=self.status_var)
        self.status_label.pack(anchor="w")
        ToolTip(self.status_label, "Current status of the conversion process. Shows progress through text chunks and batch processing steps.")
        
        # Timer label
        self.timer_var = tk.StringVar(value="")
        self.timer_label = ttk.Label(progress_frame, textvariable=self.timer_var)
        self.timer_label.pack(anchor="w", pady=(5, 0))
        ToolTip(self.timer_label, "Time elapsed and estimated time remaining for the current conversion.")
        
        # Progress bar
        self.progress = ttk.Progressbar(progress_frame, mode='determinate', maximum=1)
        self.progress.pack(fill="x", pady=(10, 0))
        ToolTip(self.progress, "Progress of the current conversion. Updates after each batch of text chunks is processed. Batches contain multiple chunks processed in parallel.")
        self.progress.pack_forget()  # Hide initially
        
    def create_control_buttons_section(self, parent):
        """Create the control buttons section with convert and stop buttons"""
        # Control buttons
        button_frame = ttk.Frame(parent)
        button_frame.pack(fill="x", pady=(0, 10))
        
        # Center the buttons
        button_container = ttk.Frame(button_frame)
        button_container.pack(expand=True)
        
        # Convert button (disabled initially until pipeline loads)
        self.convert_btn = ttk.Button(button_container, text="Convert to Speech", command=self.convert_to_speech, state="disabled")
        self.convert_btn.pack(side="left", padx=(0, 5))
        ToolTip(self.convert_btn, "Start converting the text to speech with current settings.")
        
        # Stop button (initially disabled)
        self.stop_btn = ttk.Button(button_container, text="Stop", command=self.stop_conversion, state="disabled")
        self.stop_btn.pack(side="left")
        ToolTip(self.stop_btn, "Stop the current conversion process.")
        
    def create_console_output_section(self, parent):
        """Create the console output section with text area and scrollbar"""
        # Console output section
        console_frame = ttk.LabelFrame(parent, text="Console Output", padding="10")
        console_frame.pack(fill="both", expand=True)
        
        console_text_container = ttk.Frame(console_frame)
        console_text_container.pack(fill="both", expand=True)
        
        self.console_text = tk.Text(console_text_container, height=8, wrap="word")
        console_scrollbar = ttk.Scrollbar(console_text_container, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=console_scrollbar.set)
        
        self.console_text.pack(side="left", fill="both", expand=True)
        ToolTip(self.console_text, "Detailed output and logs from the conversion process. Shows pipeline initialization, chunk processing progress, timing estimates, and any errors or warnings.")
        
        console_scrollbar.pack(side="right", fill="y")
        
    def browse_input_file(self):
        """Open file dialog to select input text file"""
        print("Opening file dialog for input file...")
        file_path = filedialog.askopenfilename(
            title="Select Input Text File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if file_path:
            print(f"Selected input file: {file_path}")
            self.input_path_var.set(file_path)
            
            # Load file content into editor
            self.load_file_content(file_path)
            
            # Auto-set output path if not already set
            if not self.output_path_var.get():
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                extension = ".mp3" if self.convert_to_mp3_var.get() else ".wav"
                output_path = os.path.join(os.path.dirname(file_path), f"{base_name}{extension}")
                self.output_path_var.set(output_path)

            self._pull_resume_info()
            self.update_recent_files(file_path)
            
    def load_file_content(self, file_path):
        """Load the content of a text file into the editor"""
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
                # Clean unwanted unicode characters while preserving multilingual text
                content = clean_unicode_text(content)
                self.editor_text.delete(1.0, tk.END)
                self.editor_text.insert(1.0, content)
                self.editor_status_var.set(f"Loaded: {os.path.basename(file_path)}")
                self.text_editor_modified = False
                self.editor_text.edit_modified(False)
                # Store original content for newline replacement feature
                self.original_text_content = content
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file content:\n{str(e)}")
            
    def toggle_text_editor(self):
        """Toggle the visibility of the text editor"""
        if self.editor_collapsed:
            # Expand the editor
            self.editor_content_frame.pack(fill="both", expand=True, pady=(5, 0))
            self.editor_toggle_btn.config(text="▲ Edit Text Content")
            self.editor_collapsed = False
            
            # If we have a file selected, load its content
            file_path = self.input_path_var.get()
            if file_path and os.path.exists(file_path):
                self.load_file_content(file_path)
                # Reset newline replacement checkbox when loading new content
                self.replace_newlines_var.set(False)
        else:
            # Collapse the editor
            self.editor_content_frame.pack_forget()
            self.editor_toggle_btn.config(text="▼ Edit Text Content")
            self.editor_collapsed = True
            
    def save_editor_content(self):
        """Save the editor content back to the file"""
        file_path = self.input_path_var.get()
        if not file_path:
            messagebox.showwarning("Warning", "No input file selected.")
            return
            
        try:
            content = self.editor_text.get(1.0, tk.END + "-1c")  # Get all content except last newline
            with open(file_path, 'w', encoding='utf-8') as file:
                file.write(content)
            self.text_editor_modified = False
            self.editor_text.edit_modified(False)
            self.editor_status_var.set(f"Saved: {os.path.basename(file_path)}")
            messagebox.showinfo("Success", "File saved successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save file:\n{str(e)}")

    def toggle_newline_replacement(self):
        """Toggle replacement of single newlines with spaces"""
        if self.replace_newlines_var.get():
            # Store original content before replacement
            self.original_text_content = self.editor_text.get(1.0, tk.END + "-1c")
            
            # Replace single newlines with spaces, preserving double newlines
            current_content = self.original_text_content
            # First, temporarily replace double newlines with a placeholder
            temp_content = current_content.replace('\n\n', 'DOUBLE_NEWLINE_PLACEHOLDER')
            # Replace single newlines with spaces
            temp_content = temp_content.replace('\n', ' ')
            # Restore double newlines
            modified_content = temp_content.replace('DOUBLE_NEWLINE_PLACEHOLDER', '\n\n')
            
            # Update the text editor with modified content
            self.editor_text.delete(1.0, tk.END)
            self.editor_text.insert(1.0, modified_content)
        else:
            # Restore original content
            self.editor_text.delete(1.0, tk.END)
            self.editor_text.insert(1.0, self.original_text_content)
            
        # Mark as modified
        self.text_editor_modified = True
        self.editor_text.edit_modified(True)
        self.update_editor_status()

    def on_text_editor_change(self, event=None):
        """Handle changes in the text editor"""
        # Schedule update to avoid too many calls
        if hasattr(self, '_update_editor_status_job'):
            self.root.after_cancel(self._update_editor_status_job)
        self._update_editor_status_job = self.root.after(100, self.update_editor_status)
        
    def update_editor_status(self):
        """Update the editor status label"""
        if not self.editor_collapsed:
            if self.editor_text.edit_modified():
                self.editor_status_var.set("Modified (not saved to file)")
                self.text_editor_modified = True
            else:
                file_path = self.input_path_var.get()
                if file_path:
                    self.editor_status_var.set(f"Loaded: {os.path.basename(file_path)}")
                else:
                    self.editor_status_var.set("No file loaded")
                
    def browse_output_file(self):
        """Open file dialog to select output audio file"""
        print("Opening file dialog for output file...")
        
        # Determine default extension based on MP3 conversion setting
        if self.convert_to_mp3_var.get():
            default_ext = ".mp3"
            filetypes = [("MP3 files", "*.mp3"), ("WAV files", "*.wav"), ("FLAC files", "*.flac"), ("All files", "*.*")]
        else:
            default_ext = ".wav"
            filetypes = [("WAV files", "*.wav"), ("MP3 files", "*.mp3"), ("FLAC files", "*.flac"), ("All files", "*.*")]
        
        file_path = filedialog.asksaveasfilename(
            title="Save Output Audio File",
            defaultextension=default_ext,
            filetypes=filetypes
        )
        if file_path:
            print(f"Selected output file: {file_path}")
            self.output_path_var.set(file_path)

    def load_settings(self):
        """Load recent files and settings from config file"""
        # Create config directory if it doesn't exist
        os.makedirs(self.config_dir, exist_ok=True)
        
        config_file = os.path.join(self.config_dir, "settings.json")
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    settings = json.load(f)
                    
                # Load recent files
                self.recent_files = settings.get("recent_files", [])
                self.update_recent_files_menu()
                
                # Load last used settings
                last_settings = settings.get("last_settings", {})
                
                # Only set the variables if they exist (after widget creation)
                if hasattr(self, 'voice_var') and self.voice_var:
                    self.voice_var.set(last_settings.get("voice", "af_heart (A)"))
                if hasattr(self, 'speed_var') and self.speed_var:
                    self.speed_var.set(last_settings.get("speed", 1.0))
                if hasattr(self, 'sample_rate_var') and self.sample_rate_var:
                    self.sample_rate_var.set(last_settings.get("sample_rate", 24000))
                if hasattr(self, 'convert_to_mp3_var') and self.convert_to_mp3_var:
                    self.convert_to_mp3_var.set(last_settings.get("convert_to_mp3", False))
                if hasattr(self, 'mp3_bitrate_var') and self.mp3_bitrate_var:
                    self.mp3_bitrate_var.set(last_settings.get("mp3_bitrate", "192k"))
                if hasattr(self, 'threshold_var') and self.threshold_var:
                    self.threshold_var.set(last_settings.get("threshold", 0.06))
                if hasattr(self, 'margin_var') and self.margin_var:
                    self.margin_var.set(last_settings.get("margin", 25))
                if hasattr(self, 'batch_count_var') and self.batch_count_var:
                    self.batch_count_var.set(last_settings.get("batch_count", 1))
                if hasattr(self, 'language_var') and self.language_var:
                    self.language_var.set(last_settings.get("language", "American English"))
                
                # Update UI elements that depend on these settings
                # This will be done after widget creation
                
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save_settings(self):
        """Save recent files and settings to config file"""
        # Create config directory if it doesn't exist
        os.makedirs(self.config_dir, exist_ok=True)
        
        # Prepare settings to save
        settings = {
            "recent_files": self.recent_files,
            "last_settings": {
                "voice": self.voice_var.get(),
                "speed": self.speed_var.get(),
                "sample_rate": self.sample_rate_var.get(),
                "convert_to_mp3": self.convert_to_mp3_var.get(),
                "mp3_bitrate": self.mp3_bitrate_var.get(),
                "threshold": self.threshold_var.get(),
                "margin": self.margin_var.get(),
                "batch_count": self.batch_count_var.get(),
                "language": self.language_var.get()
            }
        }
        
        config_file = os.path.join(self.config_dir, "settings.json")
        print(f"Saving settings to {config_file}")
        try:
            with open(config_file, 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def update_recent_files(self, file_path):
        """Add file to recent files list (limit to 10 files)"""
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        self.recent_files.insert(0, file_path)
        self.recent_files = self.recent_files[:10]  # Keep only the last 10 files
        
        # Update the recent files menu
        self.update_recent_files_menu()

    def update_recent_files_menu(self):
        """Update the recent files menu"""
        # Clear existing menu items
        self.recent_menu.delete(0, tk.END)
        
        # Add recent files to menu
        for file_path in self.recent_files:
            self.recent_menu.add_command(
                label=os.path.basename(file_path),
                command=lambda fp=file_path: self.open_recent_file(fp)
            )
        
        # Add separator and clear option if there are recent files
        if self.recent_files:
            self.recent_menu.add_separator()
            self.recent_menu.add_command(
                label="Clear Recent Files",
                command=self.clear_recent_files
            )

    def open_recent_file(self, file_path):
        """Open a file from the recent files list"""
        if os.path.exists(file_path):
            self.input_path_var.set(file_path)
            
            # Auto-set output path if not already set
            if not self.output_path_var.get():
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                extension = ".mp3" if self.convert_to_mp3_var.get() else ".wav"
                output_path = os.path.join(os.path.dirname(file_path), f"{base_name}{extension}")
                self.output_path_var.set(output_path)
                
            self._pull_resume_info()
            self.update_recent_files(file_path)
        else:
            messagebox.showerror("Error", f"File not found: {file_path}")
            # Remove from recent files
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
                self.update_recent_files_menu()

    def clear_recent_files(self):
        """Clear the recent files list"""
        self.recent_files = []
        self.update_recent_files_menu()
        
    def update_ui_with_loaded_settings(self):
        """Update UI elements with loaded settings after widget creation"""
        self._on_language_changed()  # This will update the voice dropdown
        # Update display labels for sliders
        if hasattr(self, 'speed_var') and self.speed_var:
            self.update_speed_display(self.speed_var.get())
        if hasattr(self, 'threshold_var') and self.threshold_var:
            self.update_threshold_display(self.threshold_var.get())

    def add_to_queue(self):
        """Add current input and output files to the queue"""
        input_path = self.input_path_var.get()
        output_path = self.output_path_var.get()
        
        if not input_path:
            messagebox.showwarning("Warning", "Please select an input text file.")
            return
            
        if not output_path:
            messagebox.showwarning("Warning", "Please specify an output audio file path.")
            return
            
        if not os.path.exists(input_path):
            messagebox.showerror("Error", "Input file does not exist.")
            return
        
        # Add to queue
        queue_item = {
            'input_file': input_path,
            'output_file': output_path,
            'status': '⏳ Pending'
        }
        
        self.queue_items.append(queue_item)
        
        # Add to treeview
        self.queue_tree.insert("", "end", values=(
            os.path.basename(input_path),
            os.path.basename(output_path),
            '⏳ Pending'
        ))
        
        # Clear input and output fields
        self.input_path_var.set("")
        self.output_path_var.set("")
        
        print(f"Added to queue: {input_path} -> {output_path}")

    def clear_queue(self):
        """Clear all items from the queue"""
        if not self.queue_items:
            return
            
        result = messagebox.askyesno(
            "Clear Queue",
            "Are you sure you want to clear all items from the queue?",
            icon="warning"
        )
        
        if result:
            self.queue_items.clear()
            self.queue_tree.delete(*self.queue_tree.get_children())
            print("Queue cleared")

    def delete_selected_queue_item(self):
        """Delete the selected item from the queue"""
        selected_item = self.queue_tree.selection()
        if not selected_item:
            messagebox.showwarning("Warning", "Please select an item to delete.")
            return
            
        # Get the index of the selected item
        item_index = self.queue_tree.index(selected_item[0])
        
        # Remove from queue items list
        del self.queue_items[item_index]
        
        # Remove from treeview
        self.queue_tree.delete(selected_item[0])
        
        print(f"Deleted queue item at index {item_index}")

    def _pull_resume_info(self):
        lockfile_path = self.input_path_var.get() + ".lock"
        # Load existing lockfile if it exists
        resume_info = {}
        if os.path.exists(lockfile_path):
            try:
                with open(lockfile_path, 'r') as lf:
                    resume_info = json.load(lf)
                    # Set voice with grade if available
                    voice = resume_info['voice']
                    if voice in self.voice_data:
                        grade = self.voice_data[voice]['grade']
                        self.voice_var.set(f"{voice} ({grade})")
                    else:
                        self.voice_var.set(voice)
                    self.speed_var.set(resume_info.get('speed', 1.0))
                    self.sample_rate_var.set(resume_info.get('sample_rate', 24000))
                    self.convert_to_mp3_var.set(resume_info.get('convert_to_mp3', False))
                    self.mp3_bitrate_var.set(resume_info.get('mp3_bitrate', '192k'))
                    self.start_chunk_idx = resume_info.get('failed_chunk_index', 0) or 0
            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Warning: Error loading lockfile: {e}. Starting from beginning.")

        # Open SoundFile in append mode if resuming, otherwise write mode
        if self.start_chunk_idx > 0 and os.path.exists(self.output_path_var.get()):
            self.sf_mode = 'r+'
        else:
            print(f"Output file '{self.output_path_var.get()}' not found, cannot resume, restarting.")
            self.sf_mode = 'w'
            self.start_chunk_idx = 0 # Restart if file doesn't exist to build on

        if resume_info:
            mp3_info = f", MP3: {self.mp3_bitrate_var.get()}" if self.convert_to_mp3_var.get() else ""
            # Extract voice identifier without grade for status display
            voice_with_grade = self.voice_var.get()
            voice = voice_with_grade.split(" (")[0]  # Extract voice identifier before the grade
            self.status_var.set(f"Resuming from chunk {self.start_chunk_idx + 1} with voice {voice}, speed {self.speed_var.get()}x, sample rate {self.sample_rate_var.get()}Hz{mp3_info}...")
            
        self.root.update()

    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"Received signal {signum}, cleaning up...")
        self.app_state.set_state(AppState.ERROR)
        self.root.destroy()
                
    def _cleanup_on_exit(self):
        """Clean up resources on exit. NOTE: Runs on worker thread now!"""
        # Close any open SoundFile
        if self.current_soundfile is not None:
            try:
                self.current_soundfile.close()
                print("Sound file closed successfully")
            except Exception as e:
                print(f"Error closing SoundFile: {e}")

        # Create lockfile if state indicates we should create one and we were in the middle of conversion
        input_path = self.input_path_var.get()
        lockfile_path = input_path + ".lock"
        if self.app_state.should_create_lockfile and input_path and self.current_chunk_idx is not None:
            try:
                failure_info = {
                    'failed_chunk_index': self.current_chunk_idx,
                    'error_message': 'Interrupted by user/system shutdown',
                    # Extract voice identifier without grade
                    'voice': self.voice_var.get().split(" (")[0],
                    'speed': self.speed_var.get(),
                    'sample_rate': self.sample_rate_var.get(),
                    'convert_to_mp3': self.convert_to_mp3_var.get(),
                    'mp3_bitrate': self.mp3_bitrate_var.get(),
                    'timestamp': time.time()
                }
                with open(lockfile_path, 'w') as lf:
                    json.dump(failure_info, lf, indent=2)
                print(f"Lockfile created at {lockfile_path}")
            except Exception as e:
                print(f"Error creating lockfile: {e}")
        else:
            try:
                os.remove(lockfile_path)
            except OSError:
                pass
                
    def load_pipeline(self):
        """Load Kokoro pipelines in background"""
        def load():
            try:
                print("Initializing pipeline system...")
                self.status_var.set("Initializing pipeline system...")
                self.root.update()
                
                # Show progress bar for pipeline loading
                self.progress.pack(fill="x", pady=(10, 0))
                self.progress.configure(mode='determinate', maximum=100, value=0)
                self.root.update()  # Force UI update
                
                # We no longer create pipelines in the main app
                # Pipelines are now created in the workers
                self.pipeline_loaded = True
                
                # Initialize or update workers with the pipelines
                def update_progress(progress_msg=None, timer_msg=None, progress_value=None):
                    """Unified progress update function for both pipeline loading and conversion"""
                    self.root.after(0, lambda: (
                        self.status_var.set(progress_msg) if progress_msg is not None else None,
                        self.timer_var.set(timer_msg) if timer_msg is not None else None,
                        self.progress.configure(value=progress_value * 100) if progress_value is not None else None
                    ))
                
                convert_ui_callbacks = {
                    'start_conversion': lambda: self.root.after(0, self._start_conversion_ui),
                    'update_progress': update_progress,
                    'finish_conversion': lambda output_path: self.root.after(0, lambda: self._finish_conversion_ui(output_path)),
                    'error_conversion': lambda error_msg: self.root.after(0, lambda: self._error_conversion_ui(error_msg))
                }

                queue_ui_callbacks = {
                    'update_queue_item_status': lambda idx, status: self.root.after(0, lambda: self._update_queue_item_status(idx, status)),
                    'finish_queue_processing': lambda: self.root.after(0, self._finish_queue_processing_ui),
                }
                
                # Import here to avoid circular imports
                from convert_worker import ConvertWorker
                from queue_worker import QueueWorker
                
                # Initialize workers and handle any pipeline loading errors
                try:
                    self.convert_worker = ConvertWorker(self, ui_callbacks=convert_ui_callbacks)
                    self.queue_worker = QueueWorker(self, ui_callbacks=queue_ui_callbacks | convert_ui_callbacks)
                    
                    self.status_var.set("Pipeline system initialized. Ready to convert.")
                    # Hide progress bar when done loading
                    self.progress.pack_forget()
                    # Enable the convert and play sample buttons now that pipeline system is initialized
                    self.convert_btn.config(state="normal")
                    self.play_sample_btn.config(state="normal")
                except Exception as e:
                    # Handle worker initialization errors
                    error_msg = f"Failed to initialize pipeline system: {str(e)}"
                    self.status_var.set(error_msg)
                    # Hide progress bar on error
                    self.progress.pack_forget()
                    messagebox.showerror("Error", f"Failed to initialize pipeline system:\n{str(e)}")
                    # Reset pipeline loaded flag
                    self.pipeline_loaded = False
            except Exception as e:
                self.status_var.set(f"Error initializing pipeline system: {str(e)}")
                # Hide progress bar on error
                self.progress.pack_forget()
                messagebox.showerror("Error", f"Failed to initialize pipeline system:\n{str(e)}")
                
        threading.Thread(target=load, daemon=True).start()
        
    def convert_to_speech(self):
        """Convert text file to speech"""
        print("Starting conversion process...")
        if not self.pipeline_loaded:
            messagebox.showwarning("Warning", "Pipeline is still loading. Please wait.")
            return

        self._pull_resume_info()
        
        # Check if queue has items
        if self.queue_items:
            # Process queue
            self.app_state.set_state(AppState.PROCESSING)
            self.current_queue_index = 0
            
            if self.queue_worker is not None:
                self.convert_worker_thread = threading.Thread(target=self.queue_worker.process_queue, daemon=True)
                self.convert_worker_thread.start()
        else:
            # Process single file (original behavior)
            input_path = self.input_path_var.get()
            output_path = self.output_path_var.get()
            print("Output path: " + output_path)
            
            if not input_path:
                messagebox.showwarning("Warning", "Please select an input text file.")
                return
                
            if not output_path:
                messagebox.showwarning("Warning", "Please specify an output audio file path.")
                return
                
            if not os.path.exists(input_path):
                messagebox.showerror("Error", "Input file does not exist.")
                return
            
            # Use the edited text content
            text_content = self.editor_text.get(1.0, tk.END)
                
            # Start conversion in background thread
            self.app_state.set_state(AppState.PROCESSING)
            
            if self.convert_worker is not None:
                self.convert_worker_thread = threading.Thread(
                    target=self.convert_worker.convert_file, 
                    args=(input_path, output_path, text_content), 
                    daemon=True
                )
                self.convert_worker_thread.start()
        
    def abort_conversion_process(self):
        """Abort the current conversion process"""
        print("Pausing conversion process...")
        result = messagebox.askyesno(
            "Pause Conversion",
            "Are you sure you want to pause the conversion?\n\n"
            "A lockfile will be created, so you can always resume later.",
            icon="warning"
        )
        if result:
            # We want to create a lockfile when aborting
            self.root.after(0, self._abort_conversion_ui)
                                    
    def stop_conversion(self):
        """Stop the current conversion process without creating a lockfile"""
        print("Stopping conversion process...")
        result = messagebox.askyesno(
            "Stop Conversion",
            "Are you sure you want to stop the conversion?\n\n"
            "The incomplete audio file will be saved, but no lockfile will be created.",
            icon="warning"
        )
        if result:
            # Don't create lockfile when stopping - set to STOP
            self.app_state.set_state(AppState.STOP)
            if len(self.queue_items) > 0:
                del self.queue_items[self.current_queue_index]
                self.queue_tree.delete(self.queue_tree.get_children()[self.current_queue_index])
                self.current_queue_index = 0
            else:
                print("Queue is already empty.")
            # We'll handle the cleanup in the worker thread
            self.root.after(0, self._stop_conversion_ui)
        
    def play_sample(self):
        """Play a sample of text with the selected voice"""
        if not self.pipeline_loaded and (not self.convert_worker or not hasattr(self.convert_worker, 'pipelines') or not self.convert_worker.pipelines):
            messagebox.showwarning("Warning", "Pipeline is still loading. Please wait.")
            return
        
        # Static sample text
        sample_text = "Hello! This is a sample of how the selected voice sounds. I hope you like it!"
        
        # Get selected voice and parameters (extract voice identifier without grade)
        voice_with_grade = self.voice_var.get()
        voice = voice_with_grade.split(" (")[0]  # Extract voice identifier before the grade
        speed = self.speed_var.get()
        sample_rate = self.sample_rate_var.get()
        
        print(f"Playing sample with voice: {voice}, speed: {speed}x, sample rate: {sample_rate}Hz")
        self.status_var.set(f"Playing sample with {voice}...")
        
        # Generate audio in a separate thread to avoid blocking UI
        def generate_and_play():
            try:
                # Generate audio using the worker's pipeline
                if self.convert_worker and hasattr(self.convert_worker, 'pipelines') and self.convert_worker.pipelines:
                    # Use the worker's pipeline
                    pipeline = self.convert_worker.pipelines[0]
                else:
                    raise ValueError("No pipeline available")
                    
                audio_generator = pipeline(
                    sample_text,
                    voice=voice,
                    speed=speed
                )
                
                # Collect all audio chunks
                audio_chunks = []
                for i, (code, phonemes, audio) in enumerate(audio_generator):
                    if audio is not None:
                        audio_chunks.append(audio)
                
                if not audio_chunks:
                    raise ValueError("No audio generated")
                
                # Concatenate all audio chunks
                full_audio = np.concatenate(audio_chunks)
                
                # Resample if needed
                if sample_rate != 24000:  # Kokoro default is 24000 Hz
                    import torchaudio
                    import torch
                    # Convert numpy array to tensor
                    audio_tensor = torch.from_numpy(full_audio.T)
                    resampled_audio = torchaudio.functional.resample(audio_tensor, 24000, sample_rate)
                    full_audio = resampled_audio.T.numpy()
                
                # Create a temporary file
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                    temp_path = temp_file.name
                    
                # Save audio to temporary file
                sf.write(temp_path, full_audio, sample_rate)
                
                # Play the audio file
                wave_obj = sa.WaveObject.from_wave_file(temp_path)
                play_obj = wave_obj.play()
                play_obj.wait_done()
                
                # Clean up temporary file
                os.unlink(temp_path)
                
                # Update status
                self.root.after(0, lambda: self.status_var.set("Sample playback complete."))
                
            except Exception as e:
                error_msg = str(e)
                print(f"Error playing sample: {error_msg}")
                self.root.after(0, lambda: self.status_var.set("Error playing sample."))
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to play sample:\n{error_msg}"))
        
        threading.Thread(target=generate_and_play, daemon=True).start()
        
    

    def _update_queue_item_status(self, index, status):
        """Update the status of a queue item in the treeview"""
        # Get all items in the treeview
        children = self.queue_tree.get_children()
        if index < len(children):
            # Get the current values
            current_values = self.queue_tree.item(children[index])['values']
            # Update the status (third column)
            new_values = (current_values[0], current_values[1], status)
            self.queue_tree.item(children[index], values=new_values)

    def _finish_queue_processing_ui(self):
        """Update UI when queue processing finishes"""
        self.app_state.set_state(AppState.IDLE)
        if self.app_state.should_create_lockfile:
            self.current_queue_index = -1
        
        # Calculate total time
        if self.start_time is not None:
            total_time = time.time() - self.start_time
            total_mins, total_secs = divmod(int(total_time), 60)
            total_time_str = f"Total time: {total_mins:02d}:{total_secs:02d}"
        else:
            total_time_str = ""
            
        self._update_ui_state()
        self.status_var.set("Queue processing completed!")
        self.timer_var.set(total_time_str)
        messagebox.showinfo("Success", "Queue processing completed!")

    def _wait_for_worker(self):
        """Wait for worker thread to finish only if it should stop"""
        while (self.convert_worker_thread is not None and 
               self.convert_worker_thread.is_alive() and 
               self.app_state.is_aborted):
            print("Waiting for worker thread to exit safely...")
            time.sleep(0.1)
        
        # Reset app state to IDLE after worker has finished or when not aborted
        if self.app_state.is_aborted:
            print("Worker finished, resetting app state to IDLE")
            self.app_state.set_state(AppState.IDLE)
                        
    def _update_ui_state(self, output_path=None, error_msg=None):
        """Unified UI state management function that dispatches based on app state"""
        current_state = self.app_state.state
        
        # Set basic UI elements state based on current app state
        self.progress.configure(value=0)
        if not self.app_state.is_active:
            self.progress.pack_forget()
            self.input_entry.config(state="readonly")  # Keep input read-only
            self.browse_input_btn.config(state="normal")
            self.output_entry.config(state="normal")
            self.browse_output_btn.config(state="normal")
            self.voice_dropdown.config(state="normal")
            self.convert_btn.config(state="normal", text="Convert to Speech", command=self.convert_to_speech)
            self.stop_btn.config(state="disabled")
            # Enable queue controls
            self.add_to_queue_btn.config(state="normal")
            self.clear_queue_btn.config(state="normal")
            self.delete_selected_btn.config(state="normal")
        else:
            self.progress.pack(fill="x", pady=(10, 0))
            self.input_entry.config(state="disabled")
            self.browse_input_btn.config(state="disabled")
            self.output_entry.config(state="disabled")
            self.browse_output_btn.config(state="disabled")
            self.voice_dropdown.config(state="disabled")
            self.convert_btn.config(state="normal", text="Pause", command=self.abort_conversion_process)
            self.stop_btn.config(state="normal")
            # Disable queue controls during processing
            self.add_to_queue_btn.config(state="disabled")
            self.clear_queue_btn.config(state="disabled")
            self.delete_selected_btn.config(state="disabled")
        
        # Dispatch based on current state
        if current_state == AppState.PROCESSING:
            self.status_var.set("Converting text to speech...")
            self.timer_var.set("")  # Clear timer display
            
        elif current_state == AppState.IDLE:
            # Calculate total time if we have a start time
            if self.start_time is not None:
                total_time = time.time() - self.start_time
                total_mins, total_secs = divmod(int(total_time), 60)
                total_time_str = f"Total time: {total_mins:02d}:{total_secs:02d}"
            else:
                total_time_str = ""
                
            self.status_var.set("Conversion completed successfully!")
            self.timer_var.set(total_time_str)
            if output_path and len(self.queue_items) == 0:
                messagebox.showinfo("Success", f"Audio file created successfully:\n{output_path}")
                
        elif current_state == AppState.ERROR:
            self.status_var.set("Conversion failed")
            self.timer_var.set("")  # Clear timer display
            if error_msg:
                messagebox.showerror("Error", f"Conversion failed:\n{error_msg}")
            else:
                messagebox.showinfo("Paused", "Conversion has been paused.")
                
        elif current_state == AppState.STOP:
            self.status_var.set("Conversion ended")
            self.timer_var.set("")
            messagebox.showinfo("Stopped", "Conversion has been ended.")

    def _start_conversion_ui(self):
        """Update UI when conversion starts"""
        # State is already set in convert_to_speech method
        self._update_ui_state()
        
    def _finish_conversion_ui(self, output_path):
        """Update UI when conversion finishes successfully"""
        self.app_state.set_state(AppState.IDLE)
        self._update_ui_state(output_path=output_path)
        
    def _error_conversion_ui(self, error_msg):
        """Update UI when conversion fails"""
        self.app_state.set_state(AppState.ERROR)
        self._update_ui_state(error_msg=error_msg)
        
    def _abort_conversion_ui(self):
        """Update UI when conversion is aborted"""
        self.app_state.set_state(AppState.ERROR)
        self._update_ui_state()
        
    def _stop_conversion_ui(self):
        """Update UI when conversion is stopped"""
        # State is already set to STOP in stop_conversion
        self._update_ui_state()

    def _on_closing(self):
        """Handle window closing event"""
        print("Application closing...")
        if self.app_state.is_active:
            # Ask for confirmation if conversion is in progress
            result = messagebox.askyesno(
                "Conversion in Progress",
                "A conversion is currently in progress. Are you sure you want to exit?\n\n"
                "If you exit now, the conversion will be interrupted and you'll need to resume later.",
                icon="warning"
            )
            if not result:
                print("Close cancelled by user")
                return  # User cancelled, don't close the window
            else:
                # User confirmed, don't create lockfile when closing window
                self.app_state.set_state(AppState.STOP)

        # If no conversion in progress or user confirmed, close the application
        self.root.destroy()
        
    class ConsoleRedirector:
        """Redirect STDOUT to the console text box"""
        def __init__(self, text_widget, original_stdout):
            self.text_widget = text_widget
            self.original_stdout = original_stdout
            
        def write(self, text):
            # Insert text into the text widget
            try:
                self.text_widget.insert(tk.END, text)
                self.text_widget.see(tk.END)  # Scroll to the end
                self.text_widget.update()  # Update the GUI
                # Also write to original stdout
                self.original_stdout.write(text)
            except tk.TclError as e:
                # This can happen if the widget is destroyed while writing
                # print(f"Warning: Tkinter widget error during console redirect: {e}")
                pass
            
        def flush(self):
            # Flush the original stdout
            self.original_stdout.flush()
            
    def update_threshold_display(self, value):
        """Update the threshold value display"""
        self.threshold_value_label.config(text=f"{float(value):.2f}")
    
    def update_speed_display(self, value):
        """Update the speed value display"""
        self.speed_value_label.config(text=f"{float(value):.1f}x")
    
    def _on_mp3_checkbox_changed(self, *args):
        """Handle MP3 checkbox state change to update output file extension"""
        current_output = self.output_path_var.get()
        if current_output:
            base_name = os.path.splitext(current_output)[0]
            if self.convert_to_mp3_var.get():
                new_output = base_name + ".mp3"
            else:
                new_output = base_name + ".wav"
            self.output_path_var.set(new_output)
            
    def _on_batch_count_changed(self, *args):
        """Handle batch count changes to recreate pipelines in the worker"""
        if self.pipeline_loaded and self.convert_worker:
            self.convert_worker.recreate_pipelines()
            
    def _on_language_changed(self, *args):
        """Handle language changes to filter voices and recreate pipelines"""
        selected_language = self.language_var.get()
        lang_code = self.language_codes.get(selected_language, "a")  # Default to American English
        
        # Filter voices by selected language
        filtered_voices = [
            f"{voice} ({data['grade']})" 
            for voice, data in self.voice_data.items() 
            if data['lang_code'] == lang_code
        ]
        
        # Update voice dropdown values
        self.voice_dropdown['values'] = filtered_voices
        
        # Set first voice as default if available
        if filtered_voices and self.voice_var.get() not in filtered_voices:
            self.voice_dropdown.set(filtered_voices[0])
            
        # Recreate pipelines with new language
        if self.pipeline_loaded and self.convert_worker:
            self.convert_worker.recreate_pipelines(lang_code)

def main():
    # Use TkinterDnD root window if available
    if TKDND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = TextToSpeechApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
