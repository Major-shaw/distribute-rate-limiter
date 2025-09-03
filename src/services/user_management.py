"""
User and tier management service.

This module provides services for managing user-to-tier mappings and API key resolution
with comprehensive error handling.
"""

import time
import secrets
import logging
from typing import Optional, Tuple, Dict, Any

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
