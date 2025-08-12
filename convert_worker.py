import os
import time
import json
import traceback
import threading
from tts_generator import generate_long
import soundfile as sf


class ConvertWorker:
    """Handles conversion of a single text file to speech"""
    
    def __init__(self, app_instance, ui_callbacks=None):
        self.app = app_instance
        self.ui_callbacks = ui_callbacks or {}
        
    def convert_file(self, input_path, output_path):
        """Convert a single text file to speech"""
        try:
            print(f"Starting conversion worker for {input_path}")
            # Record start time
            start_time = time.time()
            self.app.start_time = start_time
            
            # Update UI for conversion start
            if 'start_conversion' in self.ui_callbacks:
                self.ui_callbacks['start_conversion']()
            
            # Check if pipeline is loaded
            if not self.app.pipeline_loaded:
                raise ValueError("Pipeline not loaded")
                
            # Read text file
            with open(input_path, 'r', encoding='utf-8') as f:
                text = f.read().strip() + f"""

                We have now reached the end of your audiobook. This was read to you by Kokoro-82M using the {self.app.voice_var.get()} voice, through Alexis Dumas's TTS program designed for long texts and reliability.

                Thank you!
                """
                
            if not text:
                raise ValueError("Input file is empty")
                
            # Get selected voice
            voice = self.app.voice_var.get()
                        
            # Call generate_long with the required parameters
            if self.app.sf_mode == 'r+':
                self.app.current_soundfile = sf.SoundFile(output_path, self.app.sf_mode)
            else:
                self.app.current_soundfile = sf.SoundFile(output_path, self.app.sf_mode, 24000, 1, 'PCM_16')
                
            for progress_info in generate_long(
                    self.app.pipeline,
                    text,
                    self.app.current_soundfile,
                    output_path, 
                    voice,
                    start_time,
                    self.app.start_chunk_idx,
                    self.app.sf_mode,
                    round(self.app.threshold_var.get(), 2),  # Pass threshold from UI slider
                    int(self.app.margin_var.get() * 24)  # Convert ms to samples (24000 Hz = 24 samples per ms)
            ):
                # Check if abort was requested
                if self.app.app_state.is_aborted:
                    print("Conversion aborted by user")
                    # Cleanup with the appropriate lockfile setting
                    self.app._cleanup_on_exit()
                    return False

                self.app.current_chunk_idx = progress_info['processed_chunks'] - 1
                if 'update_progress' in self.ui_callbacks:
                    self.ui_callbacks['update_progress'](
                        progress_info['progress_msg'],
                        progress_info['timer_msg'],
                        progress_info['processed_chunks'] / progress_info['total_chunks']
                    )
            
            # Update UI for successful completion
            if 'finish_conversion' in self.ui_callbacks:
                self.ui_callbacks['finish_conversion'](output_path)
            return True
            
        except Exception as e:
            # Set error state for cleanup
            self.app.app_state.set_state(self.app.app_state.ERROR)
            self.app._cleanup_on_exit()
                                        
            # Update UI for error
            traceback.print_exc()
            if 'error_conversion' in self.ui_callbacks:
                self.ui_callbacks['error_conversion'](str(e))