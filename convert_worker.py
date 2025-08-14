import os
import time
import json
import traceback
import threading
from tts_generator import generate_long
import soundfile as sf
from pydub import AudioSegment

class ConvertWorker:
    """Handles conversion of a single text file to speech"""
    
    def __init__(self, app_instance, ui_callbacks=None):
        self.app = app_instance
        self.ui_callbacks = ui_callbacks or {}
        
        # Create our own Kokoro pipelines based on batch count
        self.pipelines = []

        self.recreate_pipelines()
            
    def convert_to_mp3(self, wav_path, bitrate="192k"):
        """Convert WAV file to MP3 with specified bitrate"""
        try:
            print(f"Converting {wav_path} to MP3 with bitrate {bitrate}...")
            
            # Load the WAV file
            audio = AudioSegment.from_wav(wav_path)
            
            # Create MP3 filename
            mp3_path = os.path.splitext(wav_path)[0] + ".mp3"
            
            # Export as MP3
            audio.export(mp3_path, format="mp3", bitrate=bitrate)
            
            print(f"Successfully converted to MP3: {mp3_path}")
            return mp3_path
            
        except Exception as e:
            print(f"Error converting to MP3: {e}")
            raise
        
    def convert_file(self, input_path, output_path, text_content=None):
        output_path = os.path.splitext(output_path)[0]+'.wav' # in case the final destination file is mp3, we still wanna generate an intermediate wav 
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
                
            # Get selected voice (extract voice identifier without grade)
            voice_with_grade = self.app.voice_var.get()
            voice = voice_with_grade.split(" (")[0]  # Extract voice identifier before the grade
                        
            # Use provided text content or read from file
            if text_content is not None:
                text = text_content.strip() + f"""

                We have now reached the end of your audiobook. This was read to you by Kokoro-82M using the {voice} voice, through Alexis Dumas's TTS program designed for long texts and reliability.

                Thank you!
                """
            else:
                # Read text file
                with open(input_path, 'r', encoding='utf-8') as f:
                    text = f.read().strip() + f"""

                    We have now reached the end of your audiobook. This was read to you by Kokoro-82M using the {voice} voice, through Alexis Dumas's TTS program designed for long texts and reliability.

                    Thank you!
                    """
                        
            # Get speed and sample rate from UI
            speed = self.app.speed_var.get()
            sample_rate = self.app.sample_rate_var.get()
            
            # Call generate_long with the required parameters
            if self.app.sf_mode == 'r+':
                self.app.current_soundfile = sf.SoundFile(output_path, self.app.sf_mode)
            else:
                self.app.current_soundfile = sf.SoundFile(output_path, self.app.sf_mode, sample_rate, 1, 'PCM_16')
                
            for progress_info in generate_long(
                    self.pipelines,  # Use our list of pipelines
                    text,
                    self.app.current_soundfile,
                    output_path, 
                    voice,
                    start_time,
                    self.app.start_chunk_idx,
                    self.app.sf_mode,
                    round(self.app.threshold_var.get(), 2),  # Pass threshold from UI slider
                    int(self.app.margin_var.get() * sample_rate / 1000),  # Convert ms to samples based on sample rate
                    speed,  # Pass speed from UI slider
                    sample_rate,  # Pass sample rate from UI spinner
                    len(self.pipelines),  # Pass the actual number of pipelines we have
                    self.app.max_chunk_length_var.get()  # Pass max chunk length from UI spinner
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
            
            # Check if MP3 conversion is requested
            final_output_path = output_path
            if self.app.convert_to_mp3_var.get():
                try:
                    mp3_bitrate = self.app.mp3_bitrate_var.get()
                    final_output_path = self.convert_to_mp3(output_path, mp3_bitrate)
                    print(f"MP3 conversion completed: {final_output_path}")
                except Exception as e:
                    print(f"MP3 conversion failed, but WAV file was created successfully: {e}")
                    # Continue with WAV file as final output
            
            # Update UI for successful completion
            if 'finish_conversion' in self.ui_callbacks:
                self.ui_callbacks['finish_conversion'](final_output_path)
            return True
            
        except Exception as e:
            # Set error state for cleanup
            self.app.app_state.set_state(self.app.app_state.ERROR)
            self.app._cleanup_on_exit()
                                        
            # Update UI for error
            traceback.print_exc()
            if 'error_conversion' in self.ui_callbacks:
                self.ui_callbacks['error_conversion'](str(e))
                
    def recreate_pipelines(self, lang_code='a'):
        """Recreate pipelines based on updated batch count and language"""
        # Import KPipeline here to avoid slowing down app startup
        from kokoro import KPipeline
        
        # Create new pipelines based on batch count
        batch_count = self.app.batch_count_var.get()
        print(f"Recreating {batch_count} Kokoro pipeline(s) for ConvertWorker with language code '{lang_code}'...")
        self.pipelines = []
        
        try:
            for i in range(batch_count):
                print(f"Loading pipeline {i+1}/{batch_count}...")
                # Report progress to UI
                if 'update_progress' in self.ui_callbacks:
                    progress_msg = f"Loading pipeline {i+1}/{batch_count}..."
                    self.ui_callbacks['update_progress'](progress_msg=progress_msg, progress_value=(i + 1) / batch_count)
                pipeline = KPipeline(repo_id='hexgrad/Kokoro-82M', lang_code=lang_code)
                self.pipelines.append(pipeline)
            
            print(f"All {batch_count} Kokoro pipeline(s) recreated for ConvertWorker with language code '{lang_code}'")
        except Exception as e:
            # Handle pipeline loading errors
            error_msg = f"Failed to load Kokoro pipeline: {str(e)}"
            print(f"Error loading pipeline: {error_msg}")
            # If we have an error callback, use it to show the error in the GUI
            if 'error_conversion' in self.ui_callbacks:
                self.ui_callbacks['error_conversion'](error_msg)
            # Re-raise the exception so the caller knows about the failure
            raise
