import logging
import sys

def setup_logging():
    """
    Configures standard logging for the application.
    Azure Functions already captures root logger output, but we can standardize the format here.
    """
    logger = logging.getLogger("loan_classifier")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

logger = setup_logging()
