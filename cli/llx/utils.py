import re
import os
from pathlib import Path

def parse_file_mentions(message: str) -> str:
    """
    Find @filepath mentions in the message, read the files, and append their contents.
    """
    # Match @ followed by path characters, excluding trailing punctuation
    pattern = r'@([a-zA-Z0-9_./\\-]+)'
    mentions = re.findall(pattern, message)
    
    if not mentions:
        return message
        
    appended_content = []
    seen = set()
    
    for path_str in mentions:
        if path_str in seen:
            continue
        seen.add(path_str)
        
        path = Path(path_str)
        if path.is_file():
            try:
                content = path.read_text(encoding='utf-8')
                appended_content.append(f"\n\n--- File: {path_str} ---\n{content}")
            except Exception as e:
                appended_content.append(f"\n\n--- File: {path_str} (Error reading: {e}) ---")
                
    if appended_content:
        return message + "".join(appended_content)
    return message
