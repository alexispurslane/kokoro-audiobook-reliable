import re
import nltk
from nltk.tokenize import sent_tokenize, RegexpTokenizer
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

def split_at_breakpoints_nltk(chunk, max_chars=250):
    """Split a chunk at natural breakpoints using NLTK's RegexpTokenizer"""
    # Create a tokenizer that splits on common clause separators
    # This pattern matches sequences that end with clause separators
    clause_tokenizer = RegexpTokenizer(r'[^\n,;:]+(?:[,;:]|$)')
    
    # Tokenize the chunk into clauses
    clauses = clause_tokenizer.tokenize(chunk)
    
    # If we only have one clause or the chunk is short enough, return as is
    if len(clauses) <= 1 or len(chunk) <= max_chars:
        return [chunk]
    
    # Try to combine clauses into chunks without exceeding max_chars
    chunks = []
    current_chunk = ""
    
    for clause in clauses:
        # Clean up the clause by removing extra whitespace
        clause = clause.strip()
        
        # Test adding this clause to current chunk
        test_chunk = (current_chunk + " " + clause).strip() if current_chunk else clause
        
        # If adding this clause would exceed the limit and we already have content
        if len(test_chunk) > max_chars and current_chunk:
            # Save the current chunk
            chunks.append(current_chunk.strip())
            # Start a new chunk with this clause
            current_chunk = clause
        else:
            # Add this clause to the current chunk
            current_chunk = test_chunk
        
    
    # Add the last chunk if it exists
    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

def split_into_word_chunks_nltk(chunk, max_chars=200):
    """Split a chunk into smaller chunks based on words using NLTK"""
    try:
        # Use NLTK's word tokenizer for better word splitting
        from nltk.tokenize import word_tokenize
        words = word_tokenize(chunk)
    except:
        # Fallback to simple split if NLTK tokenizer is not available
        words = chunk.split()
        
    current_subchunk = ""
    subchunks = []
    
    for word in words:
        # For punctuation marks (single characters), don't add a space before them
        if len(word) == 1 and word in [",", ";", ":", "—", "."]:
            test_subchunk = f"{current_subchunk}{word}".strip()
        else:
            test_subchunk = f"{current_subchunk} {word}".strip()
            
        if (len(test_subchunk) <= max_chars or not current_subchunk) or len(word) == 1:
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
    
    # First, try to split using NLTK's regexp-based clause splitting
    clause_chunks = split_at_breakpoints_nltk(sentence, max_chars)
    
    # If the clause splitting was successful and didn't just return the original sentence
    if len(clause_chunks) > 1 or (len(clause_chunks) == 1 and len(clause_chunks[0]) <= max_chars):
        # Check if any chunks are still too long
        final_chunks = []
        for chunk in clause_chunks:
            if len(chunk) > max_chars:
                # If a clause is still too long, split it further using word-based splitting
                word_chunks = split_into_word_chunks_nltk(chunk, max_chars)
                final_chunks.extend(word_chunks)
            else:
                final_chunks.append(chunk)
        
        return final_chunks
    
    # If clause splitting didn't help, fall back to word-based splitting
    return split_into_word_chunks_nltk(sentence, max_chars)


def split_and_prepare_text(text, max_chunk_length=200):
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
            if len(sentence) > max_chunk_length:
                sentence_chunks = split_long_sentence(sentence, max_chars=max_chunk_length)
                        
            # Add sentence chunks to main chunks list
            chunks.extend(sentence_chunks)
            
            # Add a special stop token to mark the end of this sentence
            # This will be used to insert pauses between sentences
            chunks.append("SENTENCE_END_PAUSE_MARKER")
        
        # Add an extra pause marker between paragraphs
        chunks.append("SENTENCE_END_PAUSE_MARKER")
                    
    return chunks
