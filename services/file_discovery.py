import os
from typing import List
from utils.validation import is_supported_file

def discover_files(directory_path: str) -> List[str]:
    """
    Recursively discovers all supported files within the given directory.
    Returns a list of absolute file paths.
    """
    supported_files = []
    for root, _, files in os.walk(directory_path):
        for file in files:
            if is_supported_file(file):
                supported_files.append(os.path.join(root, file))
    return supported_files
