import re
import nltk
from nltk.tokenize import sent_tokenize
import unicodedata

def is_unwanted_unicode(char):
    """Check if a character is an unwanted unicode character (blocks, emoji, invisible characters, etc.)"""
    # Get the unicode category of the character
    category = unicodedata.category(char)
    
    # Check for control characters (except common whitespace)
    if category.startswith('C') and char not in ['\n', '\r', '\t']:
        return True
    
    # Check for surrogate pairs and private use characters
    if category.startswith('S'):
        return True
        
    # Check for variation selectors
    if category == 'Mn' and unicodedata.name(char, '').startswith('VARIATION SELECTOR'):
        return True
        
    # Check for specific unicode ranges that are typically unwanted
    code = ord(char)
    
    # Emojis and symbols
    if (0x1F300 <= code <= 0x1F9FF or  # Emoticons, Symbols, Transport, Miscellaneous
        0x2600 <= code <= 0x26FF or    # Miscellaneous Symbols
        0x2700 <= code <= 0x27BF or    # Dingbats
        0xFE00 <= code <= 0xFE0F or    # Variation Selectors
        0x1F1E6 <= code <= 0x1F1FF or  # Regional indicator symbols
        0x1F600 <= code <= 0x1F64F or  # Emoticons
        0x1F680 <= code <= 0x1F6FF or  # Transport and Map Symbols
        0x1F700 <= code <= 0x1F77F or  # Alchemical Symbols
        0x1F780 <= code <= 0x1F7FF or  # Geometric Shapes Extended
        0x1F800 <= code <= 0x1F8FF or  # Supplemental Arrows-C
        0x1F900 <= code <= 0x1F9FF or  # Supplemental Symbols and Pictographs
        0x1FA00 <= code <= 0x1FA6F or  # Chess Symbols
        0x1FA70 <= code <= 0x1FAFF or  # Symbols and Pictographs Extended-A
        0x2B00 <= code <= 0x2BFF or    # Miscellaneous Symbols and Arrows
        0x2300 <= code <= 0x23FF):     # Miscellaneous Technical
        return True
    
    # Zero-width characters
    if code in [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]:
        return True
        
    return False

def clean_unicode_text(text):
    """Remove unwanted unicode characters while preserving multilingual text"""
    # Filter out unwanted unicode characters
    cleaned_text = ''.join(char for char in text if not is_unwanted_unicode(char))
    return cleaned_text

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
    breakpoints = [';', ':', '–', '—', ',', ' and ', ' or ']
    
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
        ' - ': '—',
        '---': '—',
        '--': '—',
        '(': ',',
        ')': ',',
        '[': ',',
        ']': ',',
        '"': 'quote',
        '/': 'slash',
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
