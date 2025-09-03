"""
Rate limiting service module - backward compatibility layer.

This module imports and re-exports the split services for backward compatibility.
The actual implementations have been moved to separate focused modules:
- rate_limiting.py: RateLimitService
- health_management.py: HealthService
"""

from .rate_limiting import RateLimitService
from .health_management import HealthService

# Global service instances
rate_limit_service = RateLimitService()
health_service = HealthService()
