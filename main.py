import os
import traceback
import sys
import time
import json
import signal

# Check if we're already running with correct FFmpeg path and restart if needed
ffmpeg_lib_path = '/opt/homebrew/opt/ffmpeg@6/lib'
current_dyld_path = os.environ.get('DYLD_LIBRARY_PATH', '')

if ffmpeg_lib_path not in current_dyld_path:
    # Restart script with correct environment
    new_env = os.environ.copy()
    new_env['DYLD_LIBRARY_PATH'] = f"{ffmpeg_lib_path}:{current_dyld_path}" if current_dyld_path else ffmpeg_lib_path
    
    # Restart the script with correct environment
    os.execvpe(sys.executable, [sys.executable] + sys.argv, new_env)

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from kokoro import KPipeline
import threading
import soundfile as sf
from soundfile import SoundFile, SEEK_END

# Import our new modules
from tts_generator import process_chunk, generate_long

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
        
        # Track conversion state
        self.convert_worker_thread = None
        self.conversion_in_progress = False
        self.abort_conversion = threading.Event()
        self.was_error_or_force_quit = True  # Whether to create a lockfile when exiting
        
        # Set up signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Create UI elements
        self.create_widgets()
        
        # Load pipeline in background
        self.load_pipeline()
        
    def create_widgets(self):
        # Main container frame
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill="both", expand=True)
        
        # Configure root window weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # File selection section
        file_frame = ttk.LabelFrame(main_container, text="File Settings", padding="10")
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
        
        # Voice selection section
        voice_frame = ttk.LabelFrame(main_container, text="Voice Settings", padding="10")
        voice_frame.pack(fill="x", pady=(0, 10))
        
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
        
        self.voice_dropdown = ttk.Combobox(voice_frame, textvariable=self.voice_var, values=voices, state="readonly", width=30)
        self.voice_dropdown.pack(pady=(5, 0))
        self.voice_dropdown.set("af_heart")  # Set default value
        
        # Audio processing settings section
        settings_frame = ttk.LabelFrame(main_container, text="Audio Processing Settings", padding="10")
        settings_frame.pack(fill="x", pady=(0, 10))
        
        # Leading silence threshold slider
        ttk.Label(settings_frame, text="Leading/Trailing Silence Trim Threshold:").pack(anchor="w")
        
        threshold_container = ttk.Frame(settings_frame)
        threshold_container.pack(fill="x", pady=(5, 0))
        
        self.threshold_var = tk.DoubleVar(value=0.04)  # Default threshold value
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
        
        self.margin_var = tk.IntVar(value=50)  # Default margin value in ms
        self.margin_spinbox = ttk.Spinbox(margin_frame, from_=0, to=500, textvariable=self.margin_var, width=10)
        self.margin_spinbox.pack(side="left")
        ttk.Label(margin_frame, text="ms").pack(side="left", padx=(5, 0))
        
        # Progress section
        progress_frame = ttk.LabelFrame(main_container, text="Progress", padding="10")
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
        self.progress = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress.pack(fill="x", pady=(10, 0))
        self.progress.pack_forget()  # Hide initially
        
        # Control buttons
        button_frame = ttk.Frame(main_container)
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
        
        # Console output section
        console_frame = ttk.LabelFrame(main_container, text="Console Output", padding="10")
        console_frame.pack(fill="both", expand=True)
        
        console_text_container = ttk.Frame(console_frame)
        console_text_container.pack(fill="both", expand=True)
        
        self.console_text = tk.Text(console_text_container, height=8, wrap="word")
        console_scrollbar = ttk.Scrollbar(console_text_container, orient="vertical", command=self.console_text.yview)
        self.console_text.configure(yscrollcommand=console_scrollbar.set)
        
        self.console_text.pack(side="left", fill="both", expand=True)
        console_scrollbar.pack(side="right", fill="y")
        
        # Redirect STDOUT to the console text box
        self.original_stdout = sys.stdout
        sys.stdout = self.ConsoleRedirector(self.console_text, self.original_stdout)
        
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
                output_path = os.path.join(os.path.dirname(file_path), f"{base_name}.wav")
                self.output_path_var.set(output_path)

            self._pull_resume_info()
                
    def browse_output_file(self):
        """Open file dialog to select output audio file"""
        print("Opening file dialog for output file...")
        file_path = filedialog.asksaveasfilename(
            title="Save Output Audio File",
            defaultextension=".wav",
            filetypes=[("WAV files", "*.wav"), ("MP3 files", "*.mp3"), ("FLAC files", "*.flac"), ("All files", "*.*")]
        )
        if file_path:
            print(f"Selected output file: {file_path}")
            self.output_path_var.set(file_path)


    def _pull_resume_info(self):
        lockfile_path = self.input_path_var.get() + ".lock"
        # Load existing lockfile if it exists
        resume_info = {}
        if os.path.exists(lockfile_path):
            try:
                with open(lockfile_path, 'r') as lf:
                    resume_info = json.load(lf)
                    self.voice_var.set(resume_info['voice'])
                    self.start_chunk_idx = resume_info.get('failed_chunk_index', 0) or 0
            except:
                pass  # If lockfile is corrupted, start from beginning

        # Open SoundFile in append mode if resuming, otherwise write mode
        if self.start_chunk_idx > 0 and os.path.exists(self.output_path_var.get()):
            self.sf_mode = 'r+'
        else:
            print(f"Output file '{self.output_path_var.get()}' not found, cannot resume, restarting.")
            self.sf_mode = 'w'
            self.start_chunk_idx = 0 # Restart if file doesn't exist to build on

        if resume_info:
            self.status_var.set(f"Resuming from chunk {self.start_chunk_idx + 1} with voice {self.voice_var.get()}...")
            
        self.root.update()

    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        print(f"Received signal {signum}, cleaning up...")
        self.abort_conversion.set()
        self.was_error_or_force_quit = True
        self._wait_for_worker()
        self.root.destroy()
                
    def _cleanup_on_exit(self):
        """Clean up resources on exit. NOTE: Runs on worker thread now!"""
        
        # Close any open SoundFile
        if self.current_soundfile is not None:
            try:
                self.current_soundfile.close()
                print("SoundFile closed successfully")
            except Exception as e:
                print(f"Error closing SoundFile: {e}")

        # Create lockfile if this was an error or force quit and we were in the middle of doing a conversion
        input_path = self.input_path_var.get()
        lockfile_path = input_path + ".lock"
        if self.was_error_or_force_quit and input_path and self.current_chunk_idx is not None:
            try:
                failure_info = {
                    'failed_chunk_index': self.current_chunk_idx,
                    'error_message': 'Interrupted by user/system shutdown',
                    'voice': self.voice_var.get(),
                    'timestamp': time.time()
                }
                with open(lockfile_path, 'w') as lf:
                    json.dump(failure_info, lf, indent=2)
                print(f"Lockfile created at {lockfile_path}")
            except Exception as e:
                print(f"Error creating lockfile: {e}")
        else:
            os.remove(lockfile_path)
                
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
                # Enable the convert button now that pipeline is loaded
                self.convert_btn.config(state="normal")
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
        self.convert_worker_thread = threading.Thread(target=self._convert_worker, args=(input_path, output_path), daemon=True)
        self.convert_worker_thread.start()
        
    def abort_conversion_process(self):
        """Abort the current conversion process"""
        print("Pausinging conversion process...")
        result = messagebox.askyesno(
            "Pause Conversion",
            "Are you sure you want to pause the conversion?\n\n"
            "A lockfile will be created, so you can always resume later.",
            icon="warning"
        )
        if result:
            self.abort_conversion.set()
            # We want to create a lockfile when aborting
            self.was_error_or_force_quit = True
            self._wait_for_worker()
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
            # Set abort flag to stop the conversion
            self.abort_conversion.set()
            # Don't create lockfile when stopping
            self.was_error_or_force_quit = False
            self._wait_for_worker()
            # We'll handle the cleanup in the worker thread
            self.root.after(0, self._stop_conversion_ui)
        
    def _convert_worker(self, input_path, output_path):
        """Worker function to perform conversion in background"""
        try:
            print(f"Starting conversion worker for {input_path}")
            # Record start time
            start_time = time.time()
            self.start_time = start_time
            
            # Update UI for conversion start
            self.root.after(0, self._start_conversion_ui)
            
            # Check if pipeline is loaded
            if not self.pipeline_loaded:
                raise ValueError("Pipeline not loaded")
                
            # Read text file
            with open(input_path, 'r', encoding='utf-8') as f:
                text = f.read().strip()
                
            if not text:
                raise ValueError("Input file is empty")
                
            # Get selected voice
            voice = self.voice_var.get()
                        
            # Call generate_long with the required parameters
            if os.path.exists(self.output_path_var.get()):
                self.current_soundfile = sf.SoundFile(output_path, self.sf_mode)
            else:
                self.current_soundfile = sf.SoundFile(output_path, self.sf_mode, 24000, 1, 'PCM_16')
            for progress_info in generate_long(
                    self.pipeline,
                    text,
                    self.current_soundfile,
                    output_path, 
                    voice,
                    self.start_time,
                    self.start_chunk_idx,
                    self.sf_mode,
                    round(self.threshold_var.get(), 2),  # Pass threshold from UI slider
                    int(self.margin_var.get() * 24)  # Convert ms to samples (24000 Hz = 24 samples per ms)
            ):
                # Check if abort was requested
                if self.abort_conversion.is_set():
                    print("Conversion aborted by user")
                    # Cleanup with the appropriate lockfile setting
                    self._cleanup_on_exit()
                    return

                self.current_chunk_idx = progress_info['processed_chunks'] - 1
                self.root.after(0, lambda msg=progress_info['progress_msg']: self.status_var.set(msg))
                self.root.after(0, lambda msg=progress_info['timer_msg']: self.timer_var.set(msg))
                self.root.after(0, lambda value=(progress_info['processed_chunks']/progress_info['total_chunks']): self.progress.configure(value=value))
                self.root.update()
            
            # Update UI for successful completion
            self.root.after(0, self._finish_conversion_ui, output_path)
            
        except Exception as e:
            # Only create lockfile if we want to create one
            self.was_error_or_force_quit = True
            self._wait_for_worker()
            self._cleanup_on_exit()
                                        
            # Update UI for error
            traceback.print_exc()
            self.root.after(0, self._error_conversion_ui, str(e))

    def _wait_for_worker(self):
        while self.convert_worker_thread is not None and self.convert_worker_thread.is_alive():
            print("Waiting for worker thread to exit safely...")
            time.sleep(0.1)
                        
    def _set_ui_state(self, enabled=True):
        """Set UI elements state (enabled or disabled)"""
        self.progress.configure(value=0)
        if enabled:
            self.progress.pack_forget()
            self.input_entry.config(state="readonly")  # Keep input read-only
            self.browse_input_btn.config(state="normal")
            self.output_entry.config(state="normal")
            self.browse_output_btn.config(state="normal")
            self.voice_dropdown.config(state="normal")
            self.convert_btn.config(state="normal", text="Convert to Speech", command=self.convert_to_speech)
            self.stop_btn.config(state="disabled")
        else:
            self.progress.pack(fill="x", pady=(10, 0))
            self.input_entry.config(state="disabled")
            self.browse_input_btn.config(state="disabled")
            self.output_entry.config(state="disabled")
            self.browse_output_btn.config(state="disabled")
            self.voice_dropdown.config(state="disabled")
            self.convert_btn.config(state="normal", text="Pause", command=self.abort_conversion_process)
            self.stop_btn.config(state="normal")
        
    def _start_conversion_ui(self):
        """Update UI when conversion starts"""
        self.conversion_in_progress = True
        self.abort_conversion.clear()
        self.was_error_or_force_quit = True  # Default to creating lockfile
        self._set_ui_state(enabled=False)
        self.status_var.set("Converting text to speech...")
        self.timer_var.set("")  # Clear timer display
        
    def _finish_conversion_ui(self, output_path):
        """Update UI when conversion finishes successfully"""
        self.conversion_in_progress = False
        # Calculate total time
        if hasattr(self, 'start_time'):
            total_time = time.time() - self.start_time
            total_mins, total_secs = divmod(int(total_time), 60)
            total_time_str = f"Total time: {total_mins:02d}:{total_secs:02d}"
        else:
            total_time_str = ""
            
        self._set_ui_state(enabled=True)
        self.status_var.set("Conversion completed successfully!")
        self.timer_var.set(total_time_str)
        messagebox.showinfo("Success", f"Audio file created successfully:\n{output_path}")
        
    def _error_conversion_ui(self, error_msg):
        """Update UI when conversion fails"""
        self.conversion_in_progress = False
        self._set_ui_state(enabled=True)
        self.status_var.set("Conversion failed")
        self.timer_var.set("")  # Clear timer display
        messagebox.showerror("Error", f"Conversion failed:\n{error_msg}")
        
    def _abort_conversion_ui(self):
        """Update UI when conversion is aborted"""
        self.conversion_in_progress = False
        self.abort_conversion.clear()
        self._set_ui_state(enabled=True)
        self.status_var.set("Conversion paused")
        self.timer_var.set("")
        messagebox.showinfo("Paused", "Conversion has been paused.")

    def _stop_conversion_ui(self):
        """Update UI when conversion is aborted"""
        self.conversion_in_progress = False
        self.abort_conversion.clear()
        self._set_ui_state(enabled=True)
        self.status_var.set("Conversion ended")
        self.timer_var.set("")
        messagebox.showinfo("Stopped", "Conversion has been ended.")

    def _on_closing(self):
        """Handle window closing event"""
        print("Application closing...")
        if self.conversion_in_progress:
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
                self.was_error_or_force_quit = False
                # Cleanup without creating lockfile
                self.abort_conversion.set()

        # If no conversion in progress or user confirmed, close the application
        self._wait_for_worker()
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
            except:
                pass
            
        def flush(self):
            # Flush the original stdout
            self.original_stdout.flush()
            
    def update_threshold_display(self, value):
        """Update the threshold value display"""
        self.threshold_value_label.config(text=f"{float(value):.2f}")

def main():
    root = tk.Tk()
    app = TextToSpeechApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
