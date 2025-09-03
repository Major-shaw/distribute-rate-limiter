"""
API key validation service.

This module provides services for validating API keys and handling security events
with comprehensive error handling and security features.
"""

import logging
from typing import Optional, Tuple, Dict, Any
from datetime import datetime

from ..core.models import APIKeyError, SecurityEvent
from .user_management import UserTierManager

logger = logging.getLogger(__name__)


class APIKeyValidator:
    """Validates API keys and handles security events."""
    
    def __init__(self, user_manager: UserTierManager):
        """
        Initialize API key validator.
        
        Args:
            user_manager: User tier manager instance
        """
        self.user_manager = user_manager
    
    def _is_valid_format(self, api_key: str) -> bool:
        """
        Validate API key format.
        
        Args:
            api_key: API key to validate
            
        Returns:
            True if format is valid, False otherwise
        """
        if not api_key:
            return False
        
        # Basic format validation
        if len(api_key) < 10 or len(api_key) > 200:
            return False
        
        # Check for reasonable characters (alphanumeric, underscore, hyphen)
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if not all(c in allowed_chars for c in api_key):
            return False
        
        return True
    
    def validate_api_key(self, api_key: Optional[str], request_context: Dict[str, Any]) -> Tuple[str, str]:
        """
        Validate API key and return user information.
        
        Args:
            api_key: API key to validate (can be None)
            request_context: Request context with IP, User-Agent, etc.
            
        Returns:
            Tuple of (user_id, tier)
            
        Raises:
            APIKeyError: If API key is invalid
        """
        request_id = request_context.get("request_id", "unknown")
        client_ip = request_context.get("ip_address", "unknown")
        
        logger.debug(
            f"[{request_id}] Starting API key validation",
            extra={
                "request_id": request_id,
                "client_ip": client_ip,
                "has_api_key": bool(api_key),
                "user_service_operation": "api_key_validation_start"
            }
        )
        
        # Check if API key is provided
        if api_key is None:
            logger.warning(
                f"[{request_id}] Missing API key from {client_ip}",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "error_code": "MISSING_API_KEY",
                    "user_service_operation": "api_key_missing"
                }
            )
            
            self._log_security_event(
                api_key="MISSING",
                error_code="MISSING_API_KEY",
                request_context=request_context
            )
            raise APIKeyError(
                message="Missing API key. Please provide X-API-Key header.",
                status_code=401,
                error_code="MISSING_API_KEY"
            )
        
        # Check if API key is empty after stripping
        api_key = api_key.strip()
        if not api_key:
            logger.warning(
                f"[{request_id}] Empty API key from {client_ip}",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "error_code": "EMPTY_API_KEY",
                    "user_service_operation": "api_key_empty"
                }
            )
            
            self._log_security_event(
                api_key="EMPTY",
                error_code="EMPTY_API_KEY",
                request_context=request_context
            )
            raise APIKeyError(
                message="Empty API key provided.",
                status_code=401,
                error_code="EMPTY_API_KEY"
            )
        
        api_key_preview = api_key[:10] + "..." if len(api_key) > 10 else api_key
        
        # Validate format
        logger.debug(
            f"[{request_id}] Validating API key format: {api_key_preview}",
            extra={
                "request_id": request_id,
                "api_key_preview": api_key_preview,
                "api_key_length": len(api_key),
                "user_service_operation": "api_key_format_validation"
            }
        )
        
        if not self._is_valid_format(api_key):
            logger.warning(
                f"[{request_id}] Malformed API key format from {client_ip}: {api_key[:20]}",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "api_key_preview": api_key[:20],
                    "api_key_length": len(api_key),
                    "error_code": "MALFORMED_API_KEY",
                    "user_service_operation": "api_key_format_invalid"
                }
            )
            
            self._log_security_event(
                api_key=api_key[:20],  # Log first 20 chars for debugging
                error_code="MALFORMED_API_KEY",
                request_context=request_context
            )
            raise APIKeyError(
                message="Malformed API key format.",
                status_code=400,
                error_code="MALFORMED_API_KEY"
            )
        
        # Look up user and tier
        logger.debug(
            f"[{request_id}] Looking up user and tier for API key: {api_key_preview}",
            extra={
                "request_id": request_id,
                "api_key_preview": api_key_preview,
                "user_service_operation": "api_key_lookup_start"
            }
        )
        
        user_tier_info = self.user_manager.get_user_tier(api_key)
        if not user_tier_info:
            logger.warning(
                f"[{request_id}] Invalid API key from {client_ip}: {api_key[:20]}",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "api_key_preview": api_key[:20],
                    "error_code": "INVALID_API_KEY",
                    "user_service_operation": "api_key_lookup_failed"
                }
            )
            
            self._log_security_event(
                api_key=api_key[:20],  # Log first 20 chars for debugging
                error_code="INVALID_API_KEY",
                request_context=request_context
            )
            raise APIKeyError(
                message="Invalid API key provided.",
                status_code=401,
                error_code="INVALID_API_KEY"
            )
        
        user_id, tier = user_tier_info
        
        logger.info(
            f"[{request_id}] API key validation successful: {api_key_preview} -> {user_id} ({tier})",
            extra={
                "request_id": request_id,
                "client_ip": client_ip,
                "api_key_preview": api_key_preview,
                "user_id": user_id,
                "tier": tier,
                "user_service_operation": "api_key_validation_success"
            }
        )
        logger.debug(f"Valid API key for user {user_id} (tier: {tier})")
        return user_id, tier
    
    def _log_security_event(self, api_key: str, error_code: str, 
                           request_context: Dict[str, Any]):
        """
        Log security event for monitoring.
        
        Args:
            api_key: API key (truncated for security)
            error_code: Error code
            request_context: Request context information
        """
        event = SecurityEvent(
            event_type="api_key_validation_failed",
            api_key_prefix=api_key[:8] + "..." if len(api_key) > 8 else api_key,
            ip_address=request_context.get("ip_address", "unknown"),
            user_agent=request_context.get("user_agent", "unknown"),
            error_code=error_code,
            request_id=request_context.get("request_id")
        )
        
        # Log as structured data
        logger.warning(
            "Invalid API key attempt",
            extra={
                "event": event.dict(),
                "security_alert": True
            }
        )
