import contextlib
import os
import time
import soundfile as sf
import numpy as np
from text_processor import split_and_prepare_text
import threading

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
        start_idx = max(0, non_silence_indices[0] - margin*2)
        end_idx = min(len(audio_data), non_silence_indices[-1] + margin*10)
        return audio_data[start_idx:end_idx]
    else:
        return audio_data

def process_chunk(pipeline, chunk, voice, threshold=0.06, margin=10, speed=1.0, sample_rate=24000):
    """Process a single chunk and return the audio tensor or a pause marker"""
    # Import torch here to avoid slowing down app startup
    import torch
    
    # Check if this is a pause marker
    if chunk == "SENTENCE_END_PAUSE_MARKER":
        # Create a pause instead of audio
        pause = torch.zeros((int(sample_rate*0.5), 1))  # shape (time, channels)
        return pause.cpu().numpy()
    
    max_retries = 10
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            # Generate audio for this chunk using kokoro
            # Kokoro's API returns a generator, so we need to extract the audio
            generator = pipeline(chunk, voice=voice, speed=speed)
            # Get the first (and typically only) result from the generator
            result = next(generator)
            # The result is a tuple (grapheme_segment, phoneme_segment, audio_tensor)
            audio = result[2]  # Extract the audio tensor
            
            # Ensure audio tensor has the correct shape (time, channels)
            if audio.dim() == 1:
                audio = audio.unsqueeze(1)  # Add channel dimension if missing
            elif audio.dim() == 2 and audio.shape[0] < audio.shape[1]:
                audio = audio.T  # Transpose if needed to get (time, channels) format
            
            # Resample audio if needed
            if sample_rate != 24000:  # Kokoro default is 24000 Hz
                import torchaudio
                audio = torchaudio.functional.resample(audio.T, 24000, sample_rate).T
            
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


def generate_long(pipelines, text, current_soundfile, output_path, voice='af_heart', start_time=None, start_chunk_idx=0, sf_mode='w', threshold=0.06, margin=10, speed=1.0, sample_rate=24000, batch_count=1, max_chunk_length=200):
    """Generate long-form speech with resume capability and parallel batch processing"""
    # Get lockfile path
    lockfile_path = output_path + ".lock"
    print(f"Batch count: {batch_count}")
    
    # Set initial state
    sentence_chunks = split_and_prepare_text(text, max_chunk_length)
    print("\n".join(sentence_chunks))
    
    total_chunks = len(sentence_chunks)
    
    with current_soundfile as f:
        # If resuming, seek to end of file
        if sf_mode == 'r+':
            f.seek(0, sf.SEEK_END)
        
        # Process chunks in batches using for loop and slices
        for batch_start_idx in range(start_chunk_idx, len(sentence_chunks), batch_count):
            # Determine the end index for this batch
            batch_end_idx = min(batch_start_idx + batch_count, len(sentence_chunks))
            
            # Get chunks for this batch
            batch_chunks = sentence_chunks[batch_start_idx:batch_end_idx]
            
            # Process chunks in parallel using threading
            batch_results = [None] * len(batch_chunks)
            threads = []
            
            def process_chunk_thread(i, chunk):
                # Use the corresponding pipeline for this chunk (round-robin)
                pipeline_idx = i % len(pipelines)
                result = process_chunk(pipelines[pipeline_idx], chunk, voice, threshold=threshold, margin=margin, speed=speed, sample_rate=sample_rate)
                batch_results[i] = result
            
            # Create and start threads for each chunk in the batch
            for i, chunk in enumerate(batch_chunks):
                thread = threading.Thread(target=process_chunk_thread, args=(i, chunk))
                threads.append(thread)
                thread.start()
            
            # Wait for all threads to complete
            for thread in threads:
                thread.join()
            
            # Write results to file in order
            for i, result in enumerate(batch_results):
                if result is not None:
                    f.write(result)
                    del result
                
                # Remove lockfile if this was the last chunk that previously failed
                if batch_start_idx + i >= start_chunk_idx:
                    if os.path.exists(lockfile_path):
                        os.remove(lockfile_path)

            # After completing the entire batch, update time estimates based on batches
            # Calculate batch progress for timing estimates
            completed_batches = (batch_start_idx // batch_count) + 1
            total_batches = (total_chunks + batch_count - 1) // batch_count  # Ceiling division
            elapsed_time = time.time() - start_time if start_time else 0
            estimated_total_time = (elapsed_time / completed_batches) * total_batches if completed_batches > 0 else 0
            remaining_time = estimated_total_time - elapsed_time if completed_batches > 0 else 0
            
            # Format time for display
            elapsed_mins, elapsed_secs = divmod(int(elapsed_time), 60)
            remaining_mins, remaining_secs = divmod(int(remaining_time), 60)
            
            timer_msg = f"Elapsed: {elapsed_mins:02d}:{elapsed_secs:02d} | Remaining: {remaining_mins:02d}:{remaining_secs:02d} | Batch {completed_batches}/{total_batches}"
            
            # Create a string of the first 50 characters of concatenated non-pause chunks in this batch
            batch_text = ""

            for chunk in batch_chunks:
                if chunk != "SENTENCE_END_PAUSE_MARKER":
                    batch_text += chunk + " "
            
            batch_text_display = batch_text[:50].strip()
            if len(batch_text) > 50:
                batch_text_display += "..."
            
            # Update the last yielded progress with batch timing information
            yield {
                'progress_msg': f"Completed batch {completed_batches}/{total_batches}: {batch_text_display}",
                'timer_msg': timer_msg,
                'processed_chunks': min(batch_end_idx, total_chunks),  # Number of chunks processed so far
                'total_chunks': total_chunks
            }
