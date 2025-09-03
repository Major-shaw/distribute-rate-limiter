"""
Security rate limiting service.

This module provides security-focused rate limiting for invalid API key attempts
and IP blocking functionality.
"""

import logging

logger = logging.getLogger(__name__)


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
            # If Redis is down, do not allow the request (fail close)
            return False
    
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
