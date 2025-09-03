"""
User service module - backward compatibility layer.

This module imports and re-exports the split services for backward compatibility.
The actual implementations have been moved to separate focused modules:
- user_management.py: UserTierManager
- api_key_validation.py: APIKeyValidator  
- security_rate_limiting.py: SecurityRateLimiter
"""

from .user_management import UserTierManager
from .api_key_validation import APIKeyValidator
from .security_rate_limiting import SecurityRateLimiter
from ..core.redis_client import redis_client


# Global instances
user_tier_manager = UserTierManager()
api_key_validator = APIKeyValidator(user_tier_manager)
security_rate_limiter = SecurityRateLimiter(redis_client)
