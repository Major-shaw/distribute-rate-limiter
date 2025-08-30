"""
User and API key management services.

This module provides services for managing user-to-tier mappings and API key validation
with comprehensive error handling and security features.
"""

import time
import secrets
import logging
from typing import Optional, Tuple, Dict, Any
from datetime import datetime

from ..core.models import APIKeyError, SecurityEvent
from ..core.config import config_manager

logger = logging.getLogger(__name__)


class UserTierManager:
    """Manages user-to-tier mappings and API key resolution."""
    
    def __init__(self):
        """Initialize user tier manager."""
        self._load_users()
    
    def _load_users(self):
        """Load user and API key mappings from configuration."""
        logger.debug(
            "Loading user and API key mappings from configuration",
            extra={
                "user_service_operation": "load_users_start"
            }
        )
        
        config = config_manager.config
        self._api_key_to_user = config.api_keys.copy()
        self._user_to_tier = config.users.copy()
        
        # Count users by tier for logging
        tier_counts = {}
        for user_id, tier in self._user_to_tier.items():
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        
        logger.info(
            f"Loaded {len(self._api_key_to_user)} API keys and {len(self._user_to_tier)} users",
            extra={
                "api_key_count": len(self._api_key_to_user),
                "user_count": len(self._user_to_tier),
                "tier_distribution": tier_counts,
                "available_tiers": list(config.tiers.keys()),
                "user_service_operation": "load_users_completed"
            }
        )
    
    def reload_users(self):
        """Reload user mappings from configuration."""
        try:
            config_manager.reload_config()
            self._load_users()
            logger.info("User mappings reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload user mappings: {e}")
            return False
    
    def get_user_from_api_key(self, api_key: str) -> Optional[str]:
        """
        Get user ID from API key.
        
        Args:
            api_key: API key to lookup
            
        Returns:
            User ID if found, None otherwise
        """
        return self._api_key_to_user.get(api_key)
    
    def get_tier_from_user(self, user_id: str) -> Optional[str]:
        """
        Get tier from user ID.
        
        Args:
            user_id: User ID to lookup
            
        Returns:
            Tier name if found, None otherwise
        """
        return self._user_to_tier.get(user_id)
    
    def get_user_tier(self, api_key: str) -> Optional[Tuple[str, str]]:
        """
        Get user ID and tier from API key.
        
        Args:
            api_key: API key to lookup
            
        Returns:
            Tuple of (user_id, tier) if found, None otherwise
        """
        api_key_preview = api_key[:10] + "..." if api_key and len(api_key) > 10 else "None"
        
        logger.debug(
            f"Looking up user and tier for API key: {api_key_preview}",
            extra={
                "api_key_preview": api_key_preview,
                "has_api_key": bool(api_key),
                "user_service_operation": "get_user_tier_start"
            }
        )
        
        user_id = self.get_user_from_api_key(api_key)
        if not user_id:
            logger.debug(
                f"API key not found: {api_key_preview}",
                extra={
                    "api_key_preview": api_key_preview,
                    "lookup_result": "api_key_not_found",
                    "user_service_operation": "api_key_lookup_failed"
                }
            )
            return None
        
        tier = self.get_tier_from_user(user_id)
        if not tier:
            logger.warning(
                f"User {user_id} has no tier assigned (API key: {api_key_preview})",
                extra={
                    "user_id": user_id,
                    "api_key_preview": api_key_preview,
                    "lookup_result": "no_tier_assigned",
                    "user_service_operation": "tier_lookup_failed"
                }
            )
            return None
        
        logger.debug(
            f"API key lookup successful: {api_key_preview} -> {user_id} ({tier})",
            extra={
                "api_key_preview": api_key_preview,
                "user_id": user_id,
                "tier": tier,
                "lookup_result": "success",
                "user_service_operation": "get_user_tier_success"
            }
        )
        
        return user_id, tier
    
    def add_user(self, user_id: str, tier: str) -> bool:
        """
        Add a new user with specified tier.
        
        Args:
            user_id: User identifier
            tier: User tier
            
        Returns:
            True if successful, False otherwise
        """
        if config_manager.add_user(user_id, tier):
            self._user_to_tier[user_id] = tier
            logger.info(f"Added user {user_id} with tier {tier}")
            return True
        return False
    
    def add_api_key(self, api_key: str, user_id: str) -> bool:
        """
        Add a new API key for existing user.
        
        Args:
            api_key: API key
            user_id: User identifier
            
        Returns:
            True if successful, False otherwise
        """
        if user_id not in self._user_to_tier:
            logger.error(f"Cannot add API key: user {user_id} does not exist")
            return False
        
        if config_manager.add_api_key(api_key, user_id):
            self._api_key_to_user[api_key] = user_id
            logger.info(f"Added API key {api_key} for user {user_id}")
            return True
        return False
    
    def generate_api_key(self, user_id: str, tier: str) -> Optional[str]:
        """
        Generate a new API key for a user.
        
        Args:
            user_id: User identifier
            tier: User tier
            
        Returns:
            Generated API key if successful, None otherwise
        """
        # Create a unique API key with tier prefix and random token
        timestamp = int(time.time())
        random_token = secrets.token_urlsafe(16)
        api_key = f"{tier}_{user_id}_{timestamp}_{random_token}"
        
        if self.add_api_key(api_key, user_id):
            return api_key
        return None
    
    def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive user information.
        
        Args:
            user_id: User identifier
            
        Returns:
            Dictionary with user information
        """
        tier = self.get_tier_from_user(user_id)
        if not tier:
            return None
        
        # Find all API keys for this user
        api_keys = [key for key, uid in self._api_key_to_user.items() if uid == user_id]
        
        return {
            "user_id": user_id,
            "tier": tier,
            "api_keys": api_keys,
            "api_key_count": len(api_keys)
        }
    
    def list_users(self) -> Dict[str, Dict[str, Any]]:
        """
        List all users with their information.
        
        Returns:
            Dictionary of user information
        """
        users = {}
        for user_id in self._user_to_tier.keys():
            users[user_id] = self.get_user_info(user_id)
        return users


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
        if not api_key:
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


class SecurityRateLimiter:
    """Rate limiter for security events (invalid API key attempts)."""
    
    def __init__(self, redis_client):
        """
        Initialize security rate limiter.
        
        Args:
            redis_client: Redis client instance
        """
        self.redis_client = redis_client
    
    async def check_invalid_key_attempts(self, ip_address: str, 
                                       max_attempts: int = 10,
                                       window_minutes: int = 5) -> bool:
        """
        Check if IP has exceeded invalid API key attempt limit.
        
        Args:
            ip_address: Client IP address
            max_attempts: Maximum attempts allowed
            window_minutes: Time window in minutes
            
        Returns:
            True if within limit, False if exceeded
        """
        try:
            key_suffix = f"invalid_keys:{ip_address}"
            ttl_seconds = window_minutes * 60
            
            count = await self.redis_client.increment_security_counter(
                key_suffix=key_suffix,
                ttl_seconds=ttl_seconds
            )
            
            if count > max_attempts:
                logger.warning(f"IP {ip_address} exceeded invalid API key attempts: {count}/{max_attempts}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to check invalid key attempts for {ip_address}: {e}")
            # If Redis is down, allow the request (fail open for security checks)
            return True
    
    async def is_ip_blocked(self, ip_address: str) -> bool:
        """
        Check if IP is currently blocked.
        
        Args:
            ip_address: IP address to check
            
        Returns:
            True if blocked, False otherwise
        """
        return await self.redis_client.is_ip_blocked(ip_address)
    
    async def block_ip(self, ip_address: str, duration_minutes: int = 15):
        """
        Block IP address for specified duration.
        
        Args:
            ip_address: IP address to block
            duration_minutes: Block duration in minutes
        """
        duration_seconds = duration_minutes * 60
        await self.redis_client.block_ip(ip_address, duration_seconds)
        
        logger.warning(
            f"Blocked IP {ip_address} for {duration_minutes} minutes",
            extra={
                "security_alert": True,
                "blocked_ip": ip_address,
                "duration_minutes": duration_minutes
            }
        )


# Global instances
user_tier_manager = UserTierManager()
api_key_validator = APIKeyValidator(user_tier_manager)
