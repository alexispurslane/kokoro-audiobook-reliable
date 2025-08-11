import os
import time
import json
import torch
import soundfile as sf
import numpy as np
from kokoro import KPipeline
from text_processor import split_and_prepare_text

def trim_silence(audio_data, threshold=0.06, margin=100):
    """
    Trim leading and trailing silence from audio data
    
    Args:
        audio_data: numpy array or torch tensor
        threshold: amplitude threshold for silence detection
        margin: samples to keep before and after detected sound
    """
    
    # Find first non-silence sample
    non_silence_indices = np.where(np.abs(audio_data) > threshold)[0]
    
    # Trim leading silence with margin
    if len(non_silence_indices) > 0:
        start_idx = max(0, non_silence_indices[0] - margin)
        end_idx = min(len(audio_data), non_silence_indices[-1] + margin*15)
        return audio_data[start_idx:end_idx]
    else:
        return audio_data

def process_chunk(pipeline, chunk, voice, threshold=0.06, margin=10):
    """Process a single chunk and return the audio tensor or a pause marker"""
    # Check if this is a pause marker
    if chunk == "SENTENCE_END_PAUSE_MARKER":
        # Create a pause instead of audio
        pause = torch.zeros((int(24000*0.5), 1))  # shape (time, channels)
        return pause.cpu().numpy()
    
    max_retries = 10
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Generate audio for this chunk using kokoro
            # Kokoro's API returns a generator, so we need to extract the audio
            generator = pipeline(chunk, voice=voice)
            # Get the first (and typically only) result from the generator
            result = next(generator)
            # The result is a tuple (grapheme_segment, phoneme_segment, audio_tensor)
            audio = result[2]  # Extract the audio tensor
            
            # Ensure audio tensor has the correct shape (time, channels)
            if audio.dim() == 1:
                audio = audio.unsqueeze(1)  # Add channel dimension if missing
            elif audio.dim() == 2 and audio.shape[0] < audio.shape[1]:
                audio = audio.T  # Transpose if needed to get (time, channels) format
            
            # Load audio onto cpu, convert to numpy, trim leading silence to reduce awkward pauses, then return it.
            return trim_silence(audio.cpu().numpy(), threshold=threshold, margin=margin)
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                raise Exception(f"Failed to process chunk after {max_retries} retries: {chunk[:50]}")
            else:
                # Wait a bit before retrying
                time.sleep(1)
    
    return None


def generate_long(pipeline, text, current_soundfile, output_path, voice='af_heart', start_time=None, start_chunk_idx=0, sf_mode='w', threshold=0.06, margin=10):
    """Generate long-form speech with resume capability"""
    # Get lockfile path
    lockfile_path = output_path + ".lock"
    
    # Set initial state
    sentence_chunks = split_and_prepare_text(text)
    print("\n".join(sentence_chunks))
    
    total_chunks = len(sentence_chunks)
    
    with current_soundfile as f:
        # If resuming, seek to end of file
        if sf_mode == 'r+':
            f.seek(0, sf.SEEK_END)
        
        # Process chunks starting from start_chunk_idx
        for chunk_idx in range(start_chunk_idx, len(sentence_chunks)):
            chunk = sentence_chunks[chunk_idx]
            processed_chunks = chunk_idx + 1
            elapsed_time = time.time() - start_time if start_time else 0
            estimated_total_time = (elapsed_time / processed_chunks) * total_chunks if processed_chunks > 0 else 0
            remaining_time = estimated_total_time - elapsed_time if processed_chunks > 0 else 0
            
            # Format time for display
            elapsed_mins, elapsed_secs = divmod(int(elapsed_time), 60)
            remaining_mins, remaining_secs = divmod(int(remaining_time), 60)
            
            progress_msg = f"Processing chunk {processed_chunks}/{total_chunks}: {chunk[:50]}..."
            timer_msg = f"Elapsed: {elapsed_mins:02d}:{elapsed_secs:02d} | Remaining: {remaining_mins:02d}:{remaining_secs:02d}"
            
            result = process_chunk(pipeline, chunk, voice, threshold=threshold, margin=margin)
                
            # Write audio chunk directly to file
            if result is not None:
                f.write(result)
                del result
                    
            # Remove lockfile if this was the last chunk that previously failed
            if chunk_idx >= start_chunk_idx:
                if os.path.exists(lockfile_path):
                    os.remove(lockfile_path)

            # Yield progress information
            yield {
                'progress_msg': progress_msg,
                'timer_msg': timer_msg,
                'processed_chunks': processed_chunks,
                'total_chunks': total_chunks
            }
