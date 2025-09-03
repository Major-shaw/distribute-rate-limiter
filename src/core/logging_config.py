"""
Centralized logging configuration for the distributed rate limiter.

This module provides comprehensive logging setup with multiple handlers,
custom formatters, and structured logging for different components.
"""

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional


class CustomFormatter(logging.Formatter):
    """
    Custom formatter that adds request IDs and operation markers to log records.
    """
    
    def format(self, record):
        """Format log record with additional context information."""
        # Add request_id to the format if available
        if hasattr(record, 'request_id'):
            record.req_id = f"[{record.request_id}] "
        else:
            record.req_id = ""
        
        # Add operation markers if available
        operations = []
        for attr in ['lifecycle_stage', 'service_operation', 'redis_operation', 
                    'user_service_operation', 'config_operation']:
            if hasattr(record, attr):
                operations.append(f"{attr}={getattr(record, attr)}")
        
        if operations:
            record.operations = f" [{', '.join(operations)}]"
        else:
            record.operations = ""
        
        return super().format(record)


class RateLimitFilter(logging.Filter):
    """
    Filter to only log rate limiting related messages.
    """
    
    def filter(self, record):
        """Filter log records for rate limiting components."""
        # Log if it's from rate limiting components
        rate_limit_components = [
            'src.middleware.rate_limiter',
            'src.services.rate_limit_service',
            'src.core.redis_client'
        ]
        
        if record.name in rate_limit_components:
            return True
        
        # Check for rate limiting keywords in the message
        keywords = ['rate limit', 'rate_limit', 'remaining', 'allowed', 'exceeded']
        message = record.getMessage().lower()
        return any(keyword in message for keyword in keywords)


class LoggingConfig:
    """
    Centralized logging configuration manager.
    """
    
    def __init__(self, log_dir: Optional[str] = None, log_level: Optional[str] = None):
        """
        Initialize logging configuration.
        
        Args:
            log_dir: Directory for log files (defaults to 'logs')
            log_level: Root logging level (defaults to DEBUG)
        """
        self.log_dir = Path(log_dir or "logs")
        self.log_level = getattr(logging, (log_level or "DEBUG").upper())
        self.formatter = CustomFormatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(req_id)s%(message)s%(operations)s'
        )
    
    def setup_logging(self) -> None:
        """
        Setup comprehensive logging configuration.
        
        Creates multiple log handlers:
        - Console handler (INFO and above)
        - Main application log file (INFO and above) with rotation
        - Debug log file (DEBUG and above) with rotation  
        - Security events log file (WARNING and above)
        - Rate limiting specific log file
        """
        # Ensure logs directory exists
        self.log_dir.mkdir(exist_ok=True)
        
        # Clear any existing handlers
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        
        # Setup all handlers
        self._setup_console_handler(root_logger)
        self._setup_main_file_handler(root_logger)
        self._setup_debug_file_handler(root_logger)
        self._setup_security_file_handler(root_logger)
        self._setup_rate_limit_file_handler(root_logger)
        
        # Set root logger level
        root_logger.setLevel(self.log_level)
        
        # Configure specific loggers
        self._configure_component_loggers()
        
        # Print configuration summary
        self._print_logging_summary()
    
    def _setup_console_handler(self, root_logger: logging.Logger) -> None:
        """Setup console logging handler."""
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(self.formatter)
        root_logger.addHandler(console_handler)
    
    def _setup_main_file_handler(self, root_logger: logging.Logger) -> None:
        """Setup main application log file handler."""
        main_file_handler = RotatingFileHandler(
            self.log_dir / "rate_limiter.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
        main_file_handler.setLevel(logging.INFO)
        main_file_handler.setFormatter(self.formatter)
        root_logger.addHandler(main_file_handler)
    
    def _setup_debug_file_handler(self, root_logger: logging.Logger) -> None:
        """Setup debug log file handler."""
        debug_file_handler = RotatingFileHandler(
            self.log_dir / "rate_limiter_debug.log",
            maxBytes=50*1024*1024,  # 50MB
            backupCount=3
        )
        debug_file_handler.setLevel(logging.DEBUG)
        debug_file_handler.setFormatter(self.formatter)
        root_logger.addHandler(debug_file_handler)
    
    def _setup_security_file_handler(self, root_logger: logging.Logger) -> None:
        """Setup security events log file handler."""
        security_file_handler = RotatingFileHandler(
            self.log_dir / "security.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=10
        )
        security_file_handler.setLevel(logging.WARNING)
        security_file_handler.setFormatter(self.formatter)
        root_logger.addHandler(security_file_handler)
    
    def _setup_rate_limit_file_handler(self, root_logger: logging.Logger) -> None:
        """Setup rate limiting specific log file handler."""
        rate_limit_handler = RotatingFileHandler(
            self.log_dir / "rate_limiting.log",
            maxBytes=20*1024*1024,  # 20MB
            backupCount=5
        )
        rate_limit_handler.setLevel(logging.INFO)
        rate_limit_handler.setFormatter(self.formatter)
        rate_limit_handler.addFilter(RateLimitFilter())
        root_logger.addHandler(rate_limit_handler)
    
    def _configure_component_loggers(self) -> None:
        """Configure logging levels for specific components."""
        # Third-party libraries - reduce noise
        logging.getLogger('uvicorn').setLevel(logging.INFO)
        logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
        logging.getLogger('fastapi').setLevel(logging.INFO)
        
        # Rate limiter components - more verbose for debugging
        logging.getLogger('src.middleware.rate_limiter').setLevel(logging.DEBUG)
        logging.getLogger('src.services.rate_limit_service').setLevel(logging.DEBUG)
        logging.getLogger('src.services.user_service').setLevel(logging.DEBUG)
        logging.getLogger('src.core.redis_client').setLevel(logging.DEBUG)
        logging.getLogger('src.core.config').setLevel(logging.INFO)
    
    def _print_logging_summary(self) -> None:
        """Print logging configuration summary."""
        print(f"✓ Logging configured - logs will be written to: {self.log_dir.absolute()}")
        print("  - rate_limiter.log (INFO+)")
        print("  - rate_limiter_debug.log (DEBUG+)")
        print("  - security.log (WARNING+)")
        print("  - rate_limiting.log (rate limiting specific)")


# Convenience functions for easy setup
def setup_logging(log_dir: Optional[str] = None, log_level: Optional[str] = None) -> None:
    """
    Setup logging with default configuration.
    
    Args:
        log_dir: Directory for log files (defaults to 'logs')
        log_level: Root logging level (defaults to DEBUG)
    """
    config = LoggingConfig(log_dir=log_dir, log_level=log_level)
    config.setup_logging()


def setup_logging_from_env() -> None:
    """
    Setup logging using environment variables.
    
    Environment Variables:
        LOG_DIR: Directory for log files
        LOG_LEVEL: Root logging level (DEBUG, INFO, WARNING, ERROR)
    """
    log_dir = os.getenv('LOG_DIR')
    log_level = os.getenv('LOG_LEVEL')
    setup_logging(log_dir=log_dir, log_level=log_level)


# Create a default logger for this module
logger = logging.getLogger(__name__)
