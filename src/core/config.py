"""
Configuration management for the distributed rate limiter.

This module handles loading, validation, and management of application configuration
from JSON files and environment variables.
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any
import logging

from .models import RateLimitConfig, TierConfig, RedisConfig

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages application configuration with hot-reload capability."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to configuration file. If None, uses default path.
        """
        self.config_path = config_path or self._get_default_config_path()
        self._config: Optional[RateLimitConfig] = None
        self._load_config()
    
    def _get_default_config_path(self) -> str:
        """Get default configuration file path."""
        # Look for config file relative to project root
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent
        default_path = project_root / "config" / "rate_limits.json"
        
        # Check if config file exists, if not use environment variable
        if default_path.exists():
            return str(default_path)
        
        # Fallback to environment variable
        env_path = os.getenv("RATE_LIMITER_CONFIG_PATH")
        if env_path and Path(env_path).exists():
            return env_path
        
        # Return default path even if it doesn't exist (will be created)
        return str(default_path)
    
    def _load_config(self) -> None:
        """Load configuration from file with validation."""
        logger.info(
            f"Loading configuration from {self.config_path}",
            extra={
                "config_path": self.config_path,
                "config_operation": "load_start"
            }
        )
        
        try:
            if not Path(self.config_path).exists():
                logger.warning(
                    f"Config file not found at {self.config_path}, using defaults",
                    extra={
                        "config_path": self.config_path,
                        "config_operation": "file_not_found_using_defaults"
                    }
                )
                self._config = self._get_default_config()
                return
            
            logger.debug(
                f"Reading configuration file: {self.config_path}",
                extra={
                    "config_path": self.config_path,
                    "file_size": Path(self.config_path).stat().st_size,
                    "config_operation": "file_reading"
                }
            )
            
            with open(self.config_path, 'r') as f:
                config_data = json.load(f)
            
            logger.debug(
                f"Configuration file parsed successfully, found {len(config_data)} top-level keys",
                extra={
                    "config_keys": list(config_data.keys()),
                    "tier_count": len(config_data.get('tiers', {})),
                    "user_count": len(config_data.get('users', {})),
                    "api_key_count": len(config_data.get('api_keys', {})),
                    "config_operation": "json_parsed"
                }
            )
            
            # Apply environment variable overrides
            config_data = self._apply_env_overrides(config_data)
            
            # Validate and create config object
            self._config = RateLimitConfig(**config_data)
            
            logger.info(
                f"Configuration loaded successfully from {self.config_path}",
                extra={
                    "config_path": self.config_path,
                    "tier_count": len(self._config.tiers),
                    "user_count": len(self._config.users),
                    "api_key_count": len(self._config.api_keys),
                    "redis_host": self._config.redis.host,
                    "redis_port": self._config.redis.port,
                    "config_operation": "load_success"
                }
            )
            
        except json.JSONDecodeError as e:
            logger.error(
                f"Invalid JSON in configuration file {self.config_path}: {e}",
                extra={
                    "config_path": self.config_path,
                    "json_error": str(e),
                    "error_line": getattr(e, 'lineno', None),
                    "error_column": getattr(e, 'colno', None),
                    "config_operation": "json_parse_error"
                },
                exc_info=True
            )
            logger.info("Using default configuration due to JSON parse error")
            self._config = self._get_default_config()
            
        except Exception as e:
            logger.error(
                f"Failed to load configuration from {self.config_path}: {type(e).__name__}: {e}",
                extra={
                    "config_path": self.config_path,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "config_operation": "load_error"
                },
                exc_info=True
            )
            logger.info("Using default configuration due to load error")
            self._config = self._get_default_config()
    
    def _apply_env_overrides(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides to configuration."""
        logger.debug(
            "Applying environment variable overrides to configuration",
            extra={
                "config_operation": "env_overrides_start"
            }
        )
        
        overrides_applied = []
        
        # Redis overrides
        redis_config = config_data.get("redis", {})
        original_redis = redis_config.copy()
        
        if "REDIS_HOST" in os.environ:
            redis_config["host"] = os.environ["REDIS_HOST"]
            overrides_applied.append(f"REDIS_HOST={os.environ['REDIS_HOST']}")
            
        if "REDIS_PORT" in os.environ:
            redis_config["port"] = int(os.environ["REDIS_PORT"])
            overrides_applied.append(f"REDIS_PORT={os.environ['REDIS_PORT']}")
            
        if "REDIS_DB" in os.environ:
            redis_config["db"] = int(os.environ["REDIS_DB"])
            overrides_applied.append(f"REDIS_DB={os.environ['REDIS_DB']}")
            
        if "REDIS_PASSWORD" in os.environ:
            redis_config["password"] = os.environ["REDIS_PASSWORD"]
            overrides_applied.append("REDIS_PASSWORD=***")
            
        if "REDIS_TIMEOUT" in os.environ:
            redis_config["timeout"] = float(os.environ["REDIS_TIMEOUT"])
            overrides_applied.append(f"REDIS_TIMEOUT={os.environ['REDIS_TIMEOUT']}")
        
        config_data["redis"] = redis_config
        
        # Add admin API key from environment if provided
        if "ADMIN_API_KEY" in os.environ:
            admin_key = os.environ["ADMIN_API_KEY"]
            # Create admin user if not exists
            if "admin_user" not in config_data.get("users", {}):
                config_data.setdefault("users", {})["admin_user"] = "enterprise"
                overrides_applied.append("Added admin_user with enterprise tier")
            # Add admin API key
            config_data.setdefault("api_keys", {})[admin_key] = "admin_user"
            overrides_applied.append("ADMIN_API_KEY=***")
        
        if overrides_applied:
            logger.info(
                f"Applied {len(overrides_applied)} environment variable overrides",
                extra={
                    "overrides_count": len(overrides_applied),
                    "overrides_applied": overrides_applied,
                    "redis_host": redis_config.get("host"),
                    "redis_port": redis_config.get("port"),
                    "has_admin_key": "ADMIN_API_KEY" in os.environ,
                    "config_operation": "env_overrides_applied"
                }
            )
        else:
            logger.debug(
                "No environment variable overrides found",
                extra={
                    "config_operation": "no_env_overrides"
                }
            )
        
        return config_data
    
    def _get_default_config(self) -> RateLimitConfig:
        """Get default configuration for fallback."""
        return RateLimitConfig(
            tiers={
                "free": TierConfig(
                    base_limit=10,
                    burst_limit=20,
                    degraded_limit=2,
                    window_minutes=1
                ),
                "pro": TierConfig(
                    base_limit=100,
                    burst_limit=150,
                    degraded_limit=100,
                    window_minutes=1
                ),
                "enterprise": TierConfig(
                    base_limit=1000,
                    burst_limit=1000,
                    degraded_limit=1000,
                    window_minutes=1
                )
            },
            users={
                "demo_free_user": "free",
                "demo_pro_user": "pro",
                "demo_enterprise_user": "enterprise"
            },
            api_keys={
                "demo_free_key_123": "demo_free_user",
                "demo_free_key_456": "demo_free_user",  # Same user, multiple keys
                "demo_pro_key_789": "demo_pro_user",
                "demo_enterprise_key_abc": "demo_enterprise_user"
            },
            redis=RedisConfig()
        )
    
    @property
    def config(self) -> RateLimitConfig:
        """Get current configuration."""
        if self._config is None:
            self._load_config()
        return self._config
    
    def reload_config(self) -> bool:
        """
        Reload configuration from file.
        
        Returns:
            True if reload was successful, False otherwise.
        """
        try:
            old_config = self._config
            self._load_config()
            logger.info("Configuration reloaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")
            self._config = old_config  # Restore previous config
            return False
    
    def save_config(self, config: RateLimitConfig) -> bool:
        """
        Save configuration to file.
        
        Args:
            config: Configuration to save.
            
        Returns:
            True if save was successful, False otherwise.
        """
        try:
            # Ensure config directory exists
            config_dir = Path(self.config_path).parent
            config_dir.mkdir(parents=True, exist_ok=True)
            
            # Convert to dict and save
            config_dict = {
                "tiers": {k: v.dict() for k, v in config.tiers.items()},
                "users": config.users,
                "api_keys": config.api_keys,
                "redis": config.redis.dict()
            }
            
            with open(self.config_path, 'w') as f:
                json.dump(config_dict, f, indent=2)
            
            self._config = config
            logger.info(f"Configuration saved to {self.config_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return False
    
    def get_tier_config(self, tier: str) -> Optional[TierConfig]:
        """
        Get configuration for a specific tier.
        
        Args:
            tier: Tier name.
            
        Returns:
            TierConfig if tier exists, None otherwise.
        """
        return self.config.tiers.get(tier)
    
    def get_user_tier(self, user_id: str) -> Optional[str]:
        """
        Get tier for a specific user.
        
        Args:
            user_id: User identifier.
            
        Returns:
            Tier name if user exists, None otherwise.
        """
        return self.config.users.get(user_id)
    
    def get_user_from_api_key(self, api_key: str) -> Optional[str]:
        """
        Get user ID from API key.
        
        Args:
            api_key: API key.
            
        Returns:
            User ID if API key exists, None otherwise.
        """
        return self.config.api_keys.get(api_key)
    
    def add_user(self, user_id: str, tier: str) -> bool:
        """
        Add a new user.
        
        Args:
            user_id: User identifier.
            tier: User tier.
            
        Returns:
            True if user was added successfully, False otherwise.
        """
        if tier not in self.config.tiers:
            logger.error(f"Cannot add user {user_id}: invalid tier {tier}")
            return False
        
        new_config = self.config.copy(deep=True)
        new_config.users[user_id] = tier
        
        if self.save_config(new_config):
            logger.info(f"Added user {user_id} with tier {tier}")
            return True
        return False
    
    def add_api_key(self, api_key: str, user_id: str) -> bool:
        """
        Add a new API key for an existing user.
        
        Args:
            api_key: API key.
            user_id: User identifier.
            
        Returns:
            True if API key was added successfully, False otherwise.
        """
        if user_id not in self.config.users:
            logger.error(f"Cannot add API key {api_key}: user {user_id} does not exist")
            return False
        
        new_config = self.config.copy(deep=True)
        new_config.api_keys[api_key] = user_id
        
        if self.save_config(new_config):
            logger.info(f"Added API key {api_key} for user {user_id}")
            return True
        return False


# Global configuration manager instance
config_manager = ConfigManager()
