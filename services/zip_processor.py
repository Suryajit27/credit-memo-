import zipfile
import tempfile
import os
import shutil
from typing import Generator
from contextlib import contextmanager
from utils.logging import logger

@contextmanager
def process_zip_to_temp(zip_bytes: bytes) -> Generator[str, None, None]:
    """
    Context manager that saves the ZIP bytes to a temporary file,
    extracts it to a temporary directory, yields the path to that directory,
    and ensures everything is cleaned up afterwards.
    """
    temp_dir = tempfile.mkdtemp(prefix="loan_classifier_")
    temp_zip_path = os.path.join(temp_dir, "upload.zip")
    extract_dir = os.path.join(temp_dir, "extracted")

    try:
        # Write ZIP bytes to temp file
        with open(temp_zip_path, "wb") as f:
            f.write(zip_bytes)
        
        logger.info(f"Saved uploaded ZIP to {temp_zip_path}")

        # Extract ZIP
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            # Note: For strict security, you should validate filenames 
            # to prevent zip slip vulnerabilities before extracting.
            zip_ref.extractall(extract_dir)
            
        logger.info(f"Extracted ZIP to {extract_dir}")
        
        yield extract_dir

    finally:
        # Clean up the entire temp directory
        logger.info(f"Cleaning up temporary directory {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)
