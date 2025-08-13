import os
import sys

# Check if we're already running with correct FFmpeg path and restart if needed
ffmpeg_lib_path = '/opt/homebrew/opt/ffmpeg@6/lib'
current_dyld_path = os.environ.get('DYLD_LIBRARY_PATH', '')

if ffmpeg_lib_path not in current_dyld_path:
    # Restart script with correct environment
    new_env = os.environ.copy()
    new_env['DYLD_LIBRARY_PATH'] = f"{ffmpeg_lib_path}:{current_dyld_path}" if current_dyld_path else ffmpeg_lib_path
    
    # Restart the script with correct environment
    os.execvpe(sys.executable, [sys.executable] + sys.argv, new_env)

import time
import json
import signal
import tempfile
import simpleaudio as sa
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from kokoro import KPipeline
import threading
import numpy as np
import soundfile as sf

# Import our new modules
from tts_generator import process_chunk, generate_long
from queue_worker import QueueWorker
from convert_worker import ConvertWorker
    
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
        
        # Initialize Kokoro pipeline
        self.pipeline = None
        self.pipeline_loaded = False
        
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
        
        # Queue management
        self.queue_items = []  # List of dictionaries with input_file, output_file, status
        self.current_queue_index = -1  # Index of currently processing queue item
        
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Create UI elements
        self.create_widgets()
        
        # No initialization needed for playsound
        
        # Load pipeline in background
        self.load_pipeline()
        
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
        self.create_voice_settings_section(main_container)
        self.create_audio_processing_settings_section(main_container)
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
        self.input_entry = ttk.Entry(input_row_frame, textvariable=self.input_path_var, state="readonly")
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        self.browse_input_btn = ttk.Button(input_row_frame, text="Browse", command=self.browse_input_file)
        self.browse_input_btn.pack(side="left")
        
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
        
        self.browse_output_btn = ttk.Button(output_row_frame, text="Browse", command=self.browse_output_file)
        self.browse_output_btn.pack(side="left")
        
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
        
        self.clear_queue_btn = ttk.Button(queue_control_frame, text="Clear Queue", command=self.clear_queue)
        self.clear_queue_btn.pack(side="left", padx=(0, 5))
        
        self.delete_selected_btn = ttk.Button(queue_control_frame, text="Delete Selected", command=self.delete_selected_queue_item)
        self.delete_selected_btn.pack(side="left")
        
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
        queue_scrollbar.pack(side="right", fill="y")
        
    def create_voice_settings_section(self, parent):
        """Create the voice settings section with voice selection dropdown"""
        # Voice selection section
        voice_frame = ttk.LabelFrame(parent, text="Voice Settings", padding="10")
        voice_frame.pack(fill="x", pady=(0, 10))
        
        # Voice selection
        ttk.Label(voice_frame, text="Voice:").pack(anchor="w")
        
        self.voice_var = tk.StringVar(value="af_heart")
        
        # All available voices from Kokoro
        voices = [
            "bf_alice",
            "bf_emma",
            "bf_isabella",
            "bf_lily",
            "bm_daniel",
            "bm_fable",
            "bm_george",
            "bm_lewis",
            "af_heart",
            "af_alloy",
            "af_aoede",
            "af_bella",
            "af_jessica",
            "af_kore",
            "af_nicole",
            "af_nova",
            "af_river",
            "af_sarah",
            "af_sky",
            "am_adam",
            "am_echo",
            "am_eric",
            "am_fenrir",
            "am_liam",
            "am_michael",
            "am_onyx",
            "am_puck",
            "am_santa"
        ]
        
        # Create a frame for voice dropdown and play sample button
        voice_control_frame = ttk.Frame(voice_frame)
        voice_control_frame.pack(fill="x", pady=(5, 0))
        
        self.voice_dropdown = ttk.Combobox(voice_control_frame, textvariable=self.voice_var, values=voices, state="readonly", width=30)
        self.voice_dropdown.pack(side="left", padx=(0, 5))
        self.voice_dropdown.set("af_heart")  # Set default value
        
        # Play Sample button (disabled initially until pipeline loads)
        self.play_sample_btn = ttk.Button(voice_control_frame, text="Play Sample", command=self.play_sample, state="disabled")
        self.play_sample_btn.pack(side="left")
        
        # Voice speed slider
        ttk.Label(voice_frame, text="Voice Speed:").pack(anchor="w", pady=(10, 0))
        
        speed_container = ttk.Frame(voice_frame)
        speed_container.pack(fill="x", pady=(5, 0))
        
        self.speed_var = tk.DoubleVar(value=1.0)  # Default speed (1.0 = normal)
        self.speed_slider = ttk.Scale(speed_container, from_=0.5, to=2.0, variable=self.speed_var, orient="horizontal")
        self.speed_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
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
        ttk.Label(sample_rate_frame, text="Hz").pack(side="left", padx=(5, 0))
        
        # MP3 conversion checkbox and bitrate
        ttk.Label(voice_frame, text="MP3 Conversion:").pack(anchor="w", pady=(10, 0))
        
        mp3_frame = ttk.Frame(voice_frame)
        mp3_frame.pack(fill="x", pady=(5, 0))
        
        self.convert_to_mp3_var = tk.BooleanVar(value=False)
        self.mp3_checkbox = ttk.Checkbutton(mp3_frame, text="Convert to MP3", variable=self.convert_to_mp3_var)
        self.mp3_checkbox.pack(side="left", padx=(0, 10))
        
        ttk.Label(mp3_frame, text="Bitrate:").pack(side="left", padx=(0, 5))
        
        self.mp3_bitrate_var = tk.StringVar(value="192k")
        mp3_bitrates = ["64k", "96k", "128k", "192k", "256k", "320k"]
        self.mp3_bitrate_combo = ttk.Combobox(mp3_frame, textvariable=self.mp3_bitrate_var, values=mp3_bitrates, state="readonly", width=8)
        self.mp3_bitrate_combo.pack(side="left")
        self.mp3_bitrate_combo.set("192k")  # Set default value
        
        # Add callback to update output file extension when MP3 checkbox is toggled
        self.convert_to_mp3_var.trace_add('write', self._on_mp3_checkbox_changed)
        
    def create_audio_processing_settings_section(self, parent):
        """Create the audio processing settings section with threshold slider and margin spinbox"""
        # Audio processing settings section
        settings_frame = ttk.LabelFrame(parent, text="Audio Processing Settings", padding="10")
        settings_frame.pack(fill="x", pady=(0, 10))
        
        # Leading silence threshold slider
        ttk.Label(settings_frame, text="Leading/Trailing Silence Trim Threshold:").pack(anchor="w")
        
        threshold_container = ttk.Frame(settings_frame)
        threshold_container.pack(fill="x", pady=(5, 0))
        
        self.threshold_var = tk.DoubleVar(value=0.06)  # Default threshold value
        # Create a scale with 0.02 increments
        self.threshold_slider = ttk.Scale(threshold_container, from_=0, to=0.5, variable=self.threshold_var, orient="horizontal")
        self.threshold_slider.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
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
        ttk.Label(margin_frame, text="ms").pack(side="left", padx=(5, 0))
        
    def create_progress_section(self, parent):
        """Create the progress section with status label, timer, and progress bar"""
        # Progress section
        progress_frame = ttk.LabelFrame(parent, text="Progress", padding="10")
        progress_frame.pack(fill="x", pady=(0, 10))
        
        # Status label
        self.status_var = tk.StringVar(value="Ready to convert")
        self.status_label = ttk.Label(progress_frame, textvariable=self.status_var)
        self.status_label.pack(anchor="w")
        
        # Timer label
        self.timer_var = tk.StringVar(value="")
        self.timer_label = ttk.Label(progress_frame, textvariable=self.timer_var)
        self.timer_label.pack(anchor="w", pady=(5, 0))
        
        # Progress bar
        self.progress = ttk.Progressbar(progress_frame, mode='determinate', maximum=1)
        self.progress.pack(fill="x", pady=(10, 0))
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
        
        # Stop button (initially disabled)
        self.stop_btn = ttk.Button(button_container, text="Stop", command=self.stop_conversion, state="disabled")
        self.stop_btn.pack(side="left")
        
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
            
            # Auto-set output path if not already set
            if not self.output_path_var.get():
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                extension = ".mp3" if self.convert_to_mp3_var.get() else ".wav"
                output_path = os.path.join(os.path.dirname(file_path), f"{base_name}{extension}")
                self.output_path_var.set(output_path)

            self._pull_resume_info()
                
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
                    self.voice_var.set(resume_info['voice'])
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
            self.status_var.set(f"Resuming from chunk {self.start_chunk_idx + 1} with voice {self.voice_var.get()}, speed {self.speed_var.get()}x, sample rate {self.sample_rate_var.get()}Hz{mp3_info}...")
            
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
                    'voice': self.voice_var.get(),
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
        """Load Kokoro pipeline in background"""
        def load():
            try:
                print("Loading Kokoro pipeline...")
                self.status_var.set("Loading Kokoro pipeline...")
                self.root.update()
                
                # Initialize pipeline with default settings
                self.pipeline = KPipeline(repo_id='hexgrad/Kokoro-82M', lang_code='a') # 'a' for American English
                self.pipeline_loaded = True
                self.status_var.set("Pipeline loaded. Ready to convert.")
                # Enable the convert and play sample buttons now that pipeline is loaded
                self.convert_btn.config(state="normal")
                self.play_sample_btn.config(state="normal")
            except Exception as e:
                self.status_var.set(f"Error loading pipeline: {str(e)}")
                messagebox.showerror("Error", f"Failed to load Kokoro pipeline:\n{str(e)}")
                
        threading.Thread(target=load, daemon=True).start()

    def convert_to_speech(self):
        """Convert text file to speech"""
        print("Starting conversion process...")
        if not self.pipeline_loaded:
            messagebox.showwarning("Warning", "Pipeline is still loading. Please wait.")
            return

        self._pull_resume_info()

        convert_ui_callbacks = {
            'start_conversion': lambda: self.root.after(0, self._start_conversion_ui),
            'update_progress': lambda progress_msg, timer_msg, progress_value: self.root.after(0, lambda: (
                self.status_var.set(progress_msg),
                self.timer_var.set(timer_msg),
                self.progress.configure(value=progress_value)
            )),
            'finish_conversion': lambda output_path: self.root.after(0, lambda: self._finish_conversion_ui(output_path)),
            'error_conversion': lambda error_msg: self.root.after(0, lambda: self._error_conversion_ui(error_msg))
        }

        
        queue_ui_callbacks = {
            'update_queue_item_status': lambda idx, status: self.root.after(0, lambda: self._update_queue_item_status(idx, status)),
            'finish_queue_processing': lambda: self.root.after(0, self._finish_queue_processing_ui),
        }
        
        # Check if queue has items
        if self.queue_items:
            # Process queue
            self.app_state.set_state(AppState.PROCESSING)
            self.current_queue_index = 0
            
            queue_worker = QueueWorker(self, ui_callbacks=queue_ui_callbacks | convert_ui_callbacks)
            self.convert_worker_thread = threading.Thread(target=queue_worker.process_queue, daemon=True)
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
                
            # Start conversion in background thread
            self.app_state.set_state(AppState.PROCESSING)
            
            convert_worker = ConvertWorker(self, ui_callbacks=convert_ui_callbacks)
            self.convert_worker_thread = threading.Thread(target=convert_worker.convert_file, args=(input_path, output_path), daemon=True)
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
        if not self.pipeline_loaded:
            messagebox.showwarning("Warning", "Pipeline is still loading. Please wait.")
            return
        
        # Static sample text
        sample_text = "Hello! This is a sample of how the selected voice sounds. I hope you like it!"
        
        # Get selected voice and parameters
        voice = self.voice_var.get()
        speed = self.speed_var.get()
        sample_rate = self.sample_rate_var.get()
        
        print(f"Playing sample with voice: {voice}, speed: {speed}x, sample rate: {sample_rate}Hz")
        self.status_var.set(f"Playing sample with {voice}...")
        
        # Generate audio in a separate thread to avoid blocking UI
        def generate_and_play():
            try:
                # Generate audio using the pipeline
                audio_generator = self.pipeline(
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
                    full_audio = torchaudio.functional.resample(full_audio.T, 24000, sample_rate).T
                
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
                print(f"Error playing sample: {e}")
                self.root.after(0, lambda: self.status_var.set("Error playing sample."))
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to play sample:\n{str(e)}"))
        
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

def main():
    root = tk.Tk()
    app = TextToSpeechApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
