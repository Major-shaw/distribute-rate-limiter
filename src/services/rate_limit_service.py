"""
Rate limiting service with dynamic system health awareness.

This module provides the core rate limiting functionality with support for
dynamic limits based on system health status and user tiers.
"""

import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from ..core.models import RateLimitResult, SystemHealth, TierConfig
from ..core.config import config_manager
from ..core.redis_client import redis_client

logger = logging.getLogger(__name__)


class RateLimitService:
    """Core rate limiting service with dynamic health-aware limiting."""
    
    def __init__(self):
        """Initialize rate limiting service."""
        self.redis_client = redis_client
        self._health_cache = {}
        self._health_cache_ttl = 2  # Cache health status for 2 seconds
        self._last_health_check = 0
    
    async def check_rate_limit(self, user_id: str, tier: str) -> RateLimitResult:
        """
        Check rate limit for a user based on their tier and system health.
        
        Args:
            user_id: User identifier
            tier: User tier (free, pro, enterprise)
            
        Returns:
            RateLimitResult with decision and metadata
        """
        logger.debug(
            f"Starting rate limit check for user {user_id} with tier {tier}",
            extra={
                "user_id": user_id,
                "tier": tier,
                "service_operation": "check_rate_limit_start"
            }
        )
        
        # Get tier configuration
        tier_config = config_manager.get_tier_config(tier)
        if not tier_config:
            logger.error(
                f"No configuration found for tier: {tier} - using fallback limits",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "error": "tier_config_not_found",
                    "service_operation": "tier_config_fallback"
                }
            )
            # Fallback to minimal limits
            tier_config = TierConfig(
                base_limit=10,
                burst_limit=10,
                degraded_limit=2,
                window_minutes=1
            )
        else:
            logger.debug(
                f"Tier configuration retrieved for {tier}: base={tier_config.base_limit}, "
                f"burst={tier_config.burst_limit}, degraded={tier_config.degraded_limit}",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "tier_config": {
                        "base_limit": tier_config.base_limit,
                        "burst_limit": tier_config.burst_limit,
                        "degraded_limit": tier_config.degraded_limit,
                        "window_minutes": tier_config.window_minutes
                    },
                    "service_operation": "tier_config_loaded"
                }
            )
        
        # Get system health and determine effective limit
        logger.debug(
            f"Retrieving system health for user {user_id}",
            extra={
                "user_id": user_id,
                "tier": tier,
                "service_operation": "system_health_check_start"
            }
        )
        
        system_health = await self._get_system_health_cached()
        effective_limit = self._calculate_effective_limit(tier, tier_config, system_health)
        
        logger.info(
            f"Effective limit calculated for user {user_id}: {effective_limit} (system_health: {system_health})",
            extra={
                "user_id": user_id,
                "tier": tier,
                "system_health": system_health,
                "effective_limit": effective_limit,
                "base_limit": tier_config.base_limit,
                "burst_limit": tier_config.burst_limit,
                "degraded_limit": tier_config.degraded_limit,
                "service_operation": "effective_limit_calculated"
            }
        )
        
        # Perform rate limit check
        try:
            logger.debug(
                f"Executing Redis rate limit check for user {user_id} with limit {effective_limit}",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "effective_limit": effective_limit,
                    "window_minutes": tier_config.window_minutes,
                    "service_operation": "redis_rate_check_start"
                }
            )
            
            allowed, current_count, reset_time = await self.redis_client.check_rate_limit(
                user_id=user_id,
                limit=effective_limit,
                window_minutes=tier_config.window_minutes
            )
            
            remaining = max(0, effective_limit - current_count)
            
            result = RateLimitResult(
                allowed=allowed,
                remaining=remaining,
                reset_time=reset_time,
                limit=effective_limit,
                user_id=user_id,
                tier=tier
            )
            
            # Log rate limit decision
            logger.info(
                f"Rate limit check completed for user {user_id} (tier: {tier}): "
                f"allowed={allowed}, count={current_count}/{effective_limit}, remaining={remaining}, "
                f"system_health={system_health}, reset_in={reset_time - int(time.time())}s",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "allowed": allowed,
                    "current_count": current_count,
                    "effective_limit": effective_limit,
                    "remaining": remaining,
                    "system_health": system_health,
                    "reset_time": reset_time,
                    "reset_in_seconds": reset_time - int(time.time()),
                    "service_operation": "rate_limit_decision"
                }
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Rate limit check failed for user {user_id}: {type(e).__name__}: {e}",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "effective_limit": effective_limit,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "service_operation": "rate_limit_check_failed"
                },
                exc_info=True
            )
            
            # Fallback: allow request but with minimal remaining count
            fallback_result = RateLimitResult(
                allowed=True,
                remaining=1,
                reset_time=int(time.time()) + (tier_config.window_minutes * 60),
                limit=effective_limit,
                user_id=user_id,
                tier=tier
            )
            
            logger.warning(
                f"Using fallback rate limit result for user {user_id} due to Redis failure",
                extra={
                    "user_id": user_id,
                    "tier": tier,
                    "fallback_allowed": True,
                    "fallback_remaining": 1,
                    "fallback_limit": effective_limit,
                    "service_operation": "rate_limit_fallback"
                }
            )
            
            return fallback_result
    
    def _calculate_effective_limit(self, tier: str, tier_config: TierConfig, 
                                 system_health: str) -> int:
        """
        Calculate effective rate limit based on tier and system health.
        
        Args:
            tier: User tier
            tier_config: Tier configuration
            system_health: Current system health status
            
        Returns:
            Effective rate limit
        """
        logger.debug(
            f"Calculating effective limit for tier {tier} with system health {system_health}",
            extra={
                "tier": tier,
                "system_health": system_health,
                "available_limits": {
                    "base_limit": tier_config.base_limit,
                    "burst_limit": tier_config.burst_limit,
                    "degraded_limit": tier_config.degraded_limit
                },
                "service_operation": "effective_limit_calculation_start"
            }
        )
        
        if system_health == SystemHealth.NORMAL:
            # During normal operation, allow burst limits
            effective_limit = tier_config.burst_limit
            logger.info(
                f"NORMAL system state: Applied burst limit {effective_limit} for tier {tier}",
                extra={
                    "tier": tier,
                    "system_health": system_health,
                    "selected_limit": effective_limit,
                    "limit_type": "burst",
                    "service_operation": "limit_selection_normal"
                }
            )
            
        elif system_health == SystemHealth.DEGRADED:
            # During degraded state, apply tier-specific policies
            if tier == "free":
                # Free tier gets heavily throttled
                effective_limit = tier_config.degraded_limit
                logger.info(
                    f"DEGRADED system state: Applied degraded limit {effective_limit} for free tier (load shedding)",
                    extra={
                        "tier": tier,
                        "system_health": system_health,
                        "selected_limit": effective_limit,
                        "limit_type": "degraded",
                        "policy": "load_shedding",
                        "service_operation": "limit_selection_degraded_free"
                    }
                )
                
            elif tier in ["pro", "enterprise"]:
                # Paying customers get their SLA limits enforced
                effective_limit = tier_config.base_limit
                logger.info(
                    f"DEGRADED system state: Applied base limit {effective_limit} for tier {tier} (SLA protection)",
                    extra={
                        "tier": tier,
                        "system_health": system_health,
                        "selected_limit": effective_limit,
                        "limit_type": "base",
                        "policy": "sla_protection",
                        "service_operation": "limit_selection_degraded_paid"
                    }
                )
                
            else:
                # Unknown tier, use base limit
                effective_limit = tier_config.base_limit
                logger.warning(
                    f"DEGRADED system state: Unknown tier {tier}, using base limit {effective_limit}",
                    extra={
                        "tier": tier,
                        "system_health": system_health,
                        "selected_limit": effective_limit,
                        "limit_type": "base",
                        "policy": "unknown_tier_fallback",
                        "service_operation": "limit_selection_unknown_tier"
                    }
                )
        else:
            # Unknown health status, use base limit for safety
            effective_limit = tier_config.base_limit
            logger.warning(
                f"Unknown system health {system_health}, using base limit {effective_limit} for safety",
                extra={
                    "tier": tier,
                    "system_health": system_health,
                    "selected_limit": effective_limit,
                    "limit_type": "base",
                    "policy": "unknown_health_fallback",
                    "service_operation": "limit_selection_unknown_health"
                }
            )
        
        logger.debug(
            f"Effective limit calculation completed: {effective_limit} for tier {tier}",
            extra={
                "tier": tier,
                "system_health": system_health,
                "final_limit": effective_limit,
                "service_operation": "effective_limit_calculation_completed"
            }
        )
        
        return effective_limit
    
    async def _get_system_health_cached(self) -> str:
        """
        Get system health with local caching for performance.
        
        Returns:
            Current system health status
        """
        current_time = time.time()
        
        # Check if we have a cached value that's still valid
        if (self._health_cache and 
            current_time - self._last_health_check < self._health_cache_ttl):
            cached_status = self._health_cache.get("status", SystemHealth.NORMAL)
            logger.debug(
                f"Using cached system health status: {cached_status} "
                f"(cache age: {current_time - self._last_health_check:.1f}s)",
                extra={
                    "cached_status": cached_status,
                    "cache_age_seconds": current_time - self._last_health_check,
                    "cache_ttl": self._health_cache_ttl,
                    "service_operation": "health_cache_hit"
                }
            )
            return cached_status
        
        # Fetch fresh health status
        try:
            logger.debug(
                f"Cache expired/missing, fetching fresh system health status",
                extra={
                    "cache_age_seconds": current_time - self._last_health_check if self._health_cache else None,
                    "cache_ttl": self._health_cache_ttl,
                    "service_operation": "health_cache_miss"
                }
            )
            
            health_data = await self.redis_client.get_system_health()
            self._health_cache = health_data
            self._last_health_check = current_time
            
            status = health_data.get("status", SystemHealth.NORMAL)
            
            logger.info(
                f"Retrieved fresh system health status: {status}",
                extra={
                    "health_status": status,
                    "updated_by": health_data.get("updated_by"),
                    "health_timestamp": health_data.get("timestamp"),
                    "cache_refreshed": True,
                    "service_operation": "health_status_refreshed"
                }
            )
            
            return status
            
        except Exception as e:
            logger.error(
                f"Failed to get system health, falling back to NORMAL: {type(e).__name__}: {e}",
                extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "fallback_status": SystemHealth.NORMAL,
                    "service_operation": "health_fetch_failed"
                },
                exc_info=True
            )
            # Fallback to NORMAL status
            return SystemHealth.NORMAL
    
    async def get_user_status(self, user_id: str, tier: str) -> Dict[str, Any]:
        """
        Get comprehensive rate limit status for a user.
        
        Args:
            user_id: User identifier
            tier: User tier
            
        Returns:
            Dictionary with detailed status information
        """
        tier_config = config_manager.get_tier_config(tier)
        if not tier_config:
            return {"error": f"Invalid tier: {tier}"}
        
        try:
            # Get current rate limit status
            status_data = await self.redis_client.get_user_rate_limit_status(
                user_id=user_id,
                window_minutes=tier_config.window_minutes
            )
            
            # Get system health
            system_health = await self._get_system_health_cached()
            effective_limit = self._calculate_effective_limit(tier, tier_config, system_health)
            
            # Calculate remaining requests
            current_count = status_data.get("current_count", 0)
            remaining = max(0, effective_limit - current_count)
            
            return {
                "user_id": user_id,
                "tier": tier,
                "system_health": system_health,
                "current_count": current_count,
                "effective_limit": effective_limit,
                "remaining": remaining,
                "window_start": status_data.get("window_start"),
                "window_end": status_data.get("window_end"),
                "ttl": status_data.get("ttl"),
                "tier_config": {
                    "base_limit": tier_config.base_limit,
                    "burst_limit": tier_config.burst_limit,
                    "degraded_limit": tier_config.degraded_limit,
                    "window_minutes": tier_config.window_minutes
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get user status for {user_id}: {e}")
            return {"error": str(e)}
    
    async def reset_user_rate_limit(self, user_id: str) -> bool:
        """
        Reset rate limit for a user (admin function).
        
        Args:
            user_id: User identifier
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Note: This is a simplified reset - in production you might want
            # to delete specific keys or implement a more sophisticated reset
            logger.info(f"Rate limit reset requested for user {user_id}")
            # For now, we'll log the request. Full implementation would delete
            # the user's rate limit keys from Redis
            return True
        except Exception as e:
            logger.error(f"Failed to reset rate limit for user {user_id}: {e}")
            return False


class HealthService:
    """Service for managing system health status."""
    
    def __init__(self):
        """Initialize health service."""
        self.redis_client = redis_client
    
    async def get_system_health(self) -> Dict[str, Any]:
        """
        Get current system health status with metadata.
        
        Returns:
            Dictionary with health status and metadata
        """
        try:
            health_data = await self.redis_client.get_system_health()
            
            # Convert timestamp to readable format
            if "timestamp" in health_data:
                timestamp = int(health_data["timestamp"])
                health_data["last_updated_readable"] = datetime.fromtimestamp(timestamp).isoformat()
            
            return health_data
            
        except Exception as e:
            logger.error(f"Failed to get system health: {e}")
            return {
                "status": SystemHealth.NORMAL,
                "timestamp": str(int(time.time())),
                "error": str(e)
            }
    
    async def set_system_health(self, status: str, ttl_seconds: Optional[int] = None,
                               updated_by: Optional[str] = None) -> Dict[str, Any]:
        """
        Set system health status.
        
        Args:
            status: Health status (NORMAL or DEGRADED)
            ttl_seconds: Auto-reset TTL in seconds
            updated_by: Who updated the status
            
        Returns:
            Updated health status information
        """
        # Validate status
        if status not in [SystemHealth.NORMAL, SystemHealth.DEGRADED]:
            raise ValueError(f"Invalid health status: {status}")
        
        try:
            health_data = await self.redis_client.set_system_health(
                status=status,
                ttl_seconds=ttl_seconds,
                updated_by=updated_by
            )
            
            logger.info(
                f"System health changed to {status} by {updated_by or 'unknown'}",
                extra={
                    "system_health_change": True,
                    "new_status": status,
                    "updated_by": updated_by,
                    "ttl_seconds": ttl_seconds
                }
            )
            
            return health_data
            
        except Exception as e:
            logger.error(f"Failed to set system health to {status}: {e}")
            raise
    
    async def is_healthy(self) -> Dict[str, Any]:
        """
        Check overall system health including Redis connectivity.
        
        Returns:
            Dictionary with health check results
        """
        health_checks = {}
        overall_healthy = True
        
        # Check Redis connectivity
        try:
            redis_healthy = await self.redis_client.is_healthy()
            health_checks["redis"] = "healthy" if redis_healthy else "unhealthy"
            if not redis_healthy:
                overall_healthy = False
        except Exception as e:
            health_checks["redis"] = f"error: {e}"
            overall_healthy = False
        
        # Check configuration
        try:
            config = config_manager.config
            health_checks["config"] = "healthy"
        except Exception as e:
            health_checks["config"] = f"error: {e}"
            overall_healthy = False
        
        # Get current system health status
        try:
            system_health_data = await self.get_system_health()
            health_checks["system_health"] = system_health_data.get("status", "unknown")
        except Exception as e:
            health_checks["system_health"] = f"error: {e}"
        
        return {
            "overall_status": "healthy" if overall_healthy else "unhealthy",
            "timestamp": datetime.utcnow().isoformat(),
            "components": health_checks
        }


# Global service instances
rate_limit_service = RateLimitService()
health_service = HealthService()
