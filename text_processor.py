import re
import nltk
from nltk.tokenize import sent_tokenize


def split_at_breakpoints(chunk, breakpoints):
    """Split a chunk at natural breakpoints (commas, semicolons, etc.)"""
    # Try each breakpoint type
    for breakpoint_char in breakpoints:
        if breakpoint_char in chunk:
            # Split at this breakpoint
            parts = chunk.split(breakpoint_char, 1)
            if len(parts) == 2:
                # Reconstruct the parts with the breakpoint character
                part1 = parts[0] + breakpoint_char
                part2 = parts[1]
                
                # If both parts are non-empty, return the two parts
                if part1.strip() and part2.strip():
                    return part1.strip(), part2.strip()
                
    return None, None


def split_into_word_chunks(chunk, max_chars=200):
    """Split a chunk into smaller chunks based on words"""
    # Split chunk into words
    words = chunk.split()
    current_subchunk = ""
    subchunks = []
    
    for word in words:
        test_subchunk = f"{current_subchunk} {word}".strip()
        if len(test_subchunk) <= max_chars or not current_subchunk:
            current_subchunk = test_subchunk
        else:
            # If we have a current subchunk, save it
            if current_subchunk:
                subchunks.append(current_subchunk)
            # Start new subchunk with current word
            current_subchunk = word
    
    # Add the last subchunk if it exists
    if current_subchunk:
        subchunks.append(current_subchunk)
        
    return subchunks


def split_long_sentence(sentence, max_chars=250):
    """Split a long sentence into smaller chunks without adding pauses between them"""
    if len(sentence) <= max_chars:
        return [sentence]
    
    # Split at natural breakpoints first (commas, semicolons, etc.)
    breakpoints = [';', ':', '-', '–', '—', ',', ' and ', ' or ']
    
    # Start with the whole sentence as one chunk
    chunks = [sentence]
    
    # Process each chunk to split at natural breakpoints
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        
        # If this chunk is still too long, try to split at natural breakpoints
        if len(chunk) > max_chars:
            # Try to split at natural breakpoints
            part1, part2 = split_at_breakpoints(chunk, breakpoints)
            
            if part1 and part2:
                # Remove the original chunk
                chunks.pop(i)
                # Insert the two new chunks (in reverse order to maintain position)
                chunks.insert(i, part2)
                chunks.insert(i, part1)
                # Go back to process the first new chunk
                i -= 1
            else:
                # If no natural breakpoint was found, fall back to word-based splitting
                subchunks = split_into_word_chunks(chunk, max_chars)
                
                # Replace the original chunk with the subchunks
                chunks.pop(i)
                # Insert subchunks in reverse order to maintain position
                for subchunk in reversed(subchunks):
                    chunks.insert(i, subchunk)
                i += len(subchunks) - 1  # Adjust index for the new chunks
        i += 1
        
    return chunks


def split_and_prepare_text(text):
    """Split text into chunks suitable for Kokoro processing"""
    # Replace special characters with their word equivalents
    # This helps with TTS pronunciation
    special_char_replacements = {
        '$': 'dollar',
        '^': 'caret',
        '`': 'backtick',
        '~': 'tilde',
        '@': 'at',
        '&': 'and',
        '*': 'star',
        '_': 'underscore',
        '---': '—',
        '(': ',',
        ')': ',',
        '[': ',',
        ']': ',',
        '"': 'quote',
        '/': 'forward slash',
        '\\': 'backslash'
    }
    
    # Replace special characters
    for char, word in special_char_replacements.items():
        # Replace the character with its word equivalent, preserving spacing
        text = re.sub(re.escape(char) + '+', f' {word} ', text)
    
    # Clean up extra spaces that might have been created
    text = text.strip()  # Remove leading/trailing whitespace
    
    try:
        # Download punkt tokenizer if not already downloaded
        nltk.download('punkt_tab', quiet=False)
    except:
        pass  # If download fails, continue anyway
    
    chunks = []
    
    # First, split by newlines to handle paragraph breaks
    paragraphs = text.split('\n')
            
    for paragraph in paragraphs:
        # Skip empty paragraphs
        if not paragraph.strip():
            continue
            
        # Split each paragraph into sentences
        try:
            sentences = sent_tokenize(paragraph)
        except:
            # Fallback if NLTK punkt tokenizer is not available
            sentences = paragraph.split('. ')
            print("Warning: NLTK sentence tokenizer not available, sentence tokenization quality will be degraded.")
            
        for sentence in sentences:
            # Clean up the sentence
            sentence = re.sub(r"\s+", " ", sentence.strip())
            if not sentence:
                continue
            
            # Split long sentences into sub-chunks
            sentence_chunks = [sentence]
            if len(sentence) > 200:
                sentence_chunks = split_long_sentence(sentence)
                        
            # Add sentence chunks to main chunks list
            chunks.extend(sentence_chunks)
            
            # Add a special stop token to mark the end of this sentence
            # This will be used to insert pauses between sentences
            chunks.append("SENTENCE_END_PAUSE_MARKER")
        
        # Add an extra pause marker between paragraphs
        chunks.append("SENTENCE_END_PAUSE_MARKER")
                    
    return chunks
