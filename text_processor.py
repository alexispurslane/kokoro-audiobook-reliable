import re
import nltk
from nltk.tokenize import sent_tokenize, RegexpTokenizer
import unicodedata

def is_unwanted_unicode(char):
    """Check if a character is an unwanted unicode character (blocks, emoji, invisible characters, etc.)"""
    # Get the unicode category of the character
    category = unicodedata.category(char)

    if char in ['$']:
        return False
    
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
    # Replace special characters with their word equivalents
    # Replace special characters
    text = text.replace("\xc2", " ")
    text = text.replace("\xa0", " ")
    block_ranges = {
        'Mathematical Operators': range(0x2200, 0x22FF),
        'Supplemental Mathematical Operators': range(0x2A00, 0x2AFF),
        'Mathematical Alphanumeric Symbols': range(0x1D400, 0x1D7FF),
        'Letterlike Symbols': range(0x2100, 0x214F),
        'Miscellaneous Mathematical Symbols-A': range(0x27C0, 0x27EF),
        'Miscellaneous Mathematical Symbols-B': range(0x2980, 0x29FF),
        'Miscellaneous Technical': range(0x2300, 0x23FF),
        'Geometric Shapes': range(0x25A0, 0x25FF),
        'Combining Diacritical Marks for Symbols': range(0x20D0, 0x20EF),
        'Arabic Mathematical Alphabetic Symbols': range(0x1EE00, 0x1EEFF),
        'Superscripts and Subscripts': [0x00B2, 0x00B3, 0x00B9] + list(range(0x2070, 0x209F))
    }
    for block, ur in block_ranges.items():
        print(f"Translating {block}")
        for c in ur:
            # Replace the character with its word equivalent, preserving spacing
            try:
                text = re.sub(str(chr(c)), f' {unicodedata.name(chr(c)).lower()} ', text)
            except:
                pass
    
    cleaned_text = ''.join(char for char in text if not is_unwanted_unicode(char))
    return cleaned_text

def convert_math_and_tables(text):
    """
    Finds math formulas and tables in the text and replaces them with descriptions.
    """
    try:
        import torch
        import transformers
    except ImportError:
        print("Warning: transformers or torch not available. Skipping math/table conversion.")
        return text
    
    # Hugging Face model identifier for a small local LLM
    model_id = "unsloth/Phi-4-mini-instruct"
    
    # Initialize pipeline (this will be cached after first run)
    try:
        pipeline = transformers.pipeline(
            "text-generation",
            model=model_id,
            model_kwargs={"torch_dtype": torch.float16 if torch.cuda.is_available() else torch.float32},
            device_map="auto"
        )
    except Exception as e:
        print(f"Warning: Could not initialize model pipeline: {e}. Skipping math/table conversion.")
        return text

    # --- Prompt Template ---
    def create_chat_prompt(content_type, latex_or_markdown_content):
        """
        Creates a chat-formatted prompt for the LLM.
        """
        # System message (instructions for the assistant)
        system_message = (
            "You are an expert assistant skilled in converting technical content "
            "into clear, spoken-language descriptions. Your task is to take the "
            "provided mathematical formula or data table and describe it in plain "
            "English, as if you were reading it aloud. "
            "For formulas: Use words like 'sum', 'integral', 'square root', 'subscript', 'superscript'. "
            "Example: '$\\sum_{i=1}^n x_i^2$' becomes 'the sum from i equals 1 to n of x sub i squared'. "
            "For tables: Describe the structure and content row by row or column by column. "
            "Example: '| A | B |\n|---|---|\n| 1 | 2 |' becomes "
            "'A table with columns A and B. Row 1: A is 1, B is 2.' "
            "Be concise but accurate. Do not add explanations or text beyond the description itself. OUTPUT ONE OR TWO SENTENCES ONLY."
        )
        
        # User message (the content to be described)
        user_message = f"Please describe this {content_type}:\n\n{latex_or_markdown_content}"

        # Format using the tokenizer's chat template if available
        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ]

    # --- Processing Functions ---
    def describe_content_with_llm(content_type, content):
        """
        Sends content to the LLM and returns the description.
        """
        prompt = create_chat_prompt(content_type, content)
        return pipeline(prompt, max_new_tokens=128)[0]['generated_text'][-1]['content']

    # --- 1. Process Display Math ($$...$$) ---
    print("Processing display math ($$...$$)...")
    def replace_display_math(match):
        latex_content = match.group(1)
        # Remove \begin{...} and \end{...} statements
        cleaned_content = re.sub(r'\\begin\{.*?\}|\\end\{.*?\}', '', latex_content).strip()
        print(f"  Found display math: {cleaned_content[:50]}...")
        description = describe_content_with_llm("display math formula", cleaned_content)
        print(f"  -> Description: {description[:100]}...")
        return description  # No surrounding $$
    
    # Pattern: $$...$$ (non-greedy match)
    text = re.sub(r'\$\$(.*?)\$\$', replace_display_math, text, flags=re.DOTALL)

    # --- 2. Process Inline Math ($...$) ---
    print("Processing inline math ($...$)...")
    def replace_inline_math(match):
        latex_content = match.group(1)
        # Avoid matching single $ used elsewhere (unlikely in this text, but good practice)
        if not latex_content.strip():
             return match.group(0)  # Return original if empty
        # Remove \begin{...} and \end{...} statements
        cleaned_content = re.sub(r'\\begin\{.*?\}|\\end\{.*?\}', '', latex_content).strip()
        print(f"  Found inline math: {cleaned_content}")
        description = describe_content_with_llm("inline math formula", cleaned_content)
        print(f"  -> Description: {description}")
        return description  # No surrounding $

    # Pattern: $...$ (non-greedy match, avoid $...$...)
    text = re.sub(r'(?<!\$)\$(?!\$)(.*?)\$(?!\$)', replace_inline_math, text, flags=re.DOTALL)
    
    # --- 3. Process Tables ---
    print("Processing tables...")
    def replace_table(match):
        table_content = match.group(0)
        print(f"  Found table (length {len(table_content)})...")
        description = describe_content_with_llm("data table", table_content)
        print(f"  -> Description: {description[:100]}...")
        return description

    # Pattern for a simple markdown table:
    # Starts with a line having |
    # Followed by lines having | and ---
    # Ends with a line having |
    # This is a basic pattern and might need refinement for complex tables.
    # It captures the whole table block.
    table_pattern = r'(\|[^\n]*\|[^\n]*\n)(\|[^\n]*[-|][^\n]*\n)+(\|[^\n]*\|[^\n]*\n)'
    text = re.sub(table_pattern, replace_table, text, flags=re.MULTILINE)
    
    return text

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


def split_text(text, max_chunk_length=200):
    """Split text into chunks suitable for Kokoro processing"""
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

def apply_text_transformations(original_text_content, replace_newlines_var=None, merge_paragraphs_var=None, convert_math_var=None):
    """Apply all selected text transformations"""
    # Start with original content
    content = original_text_content
    
    # Apply newline replacement if selected
    if replace_newlines_var is not None:
        # Replace single newlines with spaces, preserving double newlines
        # First, temporarily replace double newlines with a placeholder
        temp_content = content.replace('\n\n', 'DOUBLE_NEWLINE_PLACEHOLDER')
        # Replace single newlines with spaces
        temp_content = temp_content.replace('\n', ' ')
        # Restore double newlines
        content = temp_content.replace('DOUBLE_NEWLINE_PLACEHOLDER', '\n\n')
    
    # Apply paragraph merging if selected
    if merge_paragraphs_var is not None:
        # Regex pattern to match accidentally split paragraphs:
        # Line ending with anything but punctuation, followed by two newlines,
        # followed by line beginning with anything but capital, number, or dash
        pattern = r'([^\.!?])\n\n([^[A-Z0-9\-])'
        content = re.sub(pattern, r'\1 \2', content)
        
    # Apply math and table conversion if selected
    if convert_math_var is not None:
        content = convert_math_and_tables(content)
    
    return content
