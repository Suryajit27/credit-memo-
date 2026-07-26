import azure.functions as func
import os

SUPPORTED_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff'}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

def validate_request(file_bytes: bytes) -> tuple[bool, str, bytes]:
    """
    Validates the incoming ZIP file bytes.
    Returns:
        is_valid (bool): True if valid.
        message (str): Error message if invalid.
        file_bytes (bytes): The bytes of the uploaded ZIP file if valid.
    """
    try:
        if not file_bytes or len(file_bytes) == 0:
            return False, "The uploaded file is empty.", b""
            
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            return False, f"File exceeds the maximum size limit of {MAX_FILE_SIZE_BYTES / (1024*1024)} MB.", b""

        # Check basic magic bytes for ZIP to prevent some basic malicious uploads
        if not file_bytes.startswith(b'PK\x03\x04'):
             return False, "The uploaded file is not a valid ZIP archive.", b""

        return True, "", file_bytes
    except Exception as e:
        return False, f"Error reading request: {str(e)}", b""

def is_supported_file(filename: str) -> bool:
    """Check if the given filename has a supported extension."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_EXTENSIONS
