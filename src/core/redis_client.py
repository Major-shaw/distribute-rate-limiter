"""
Redis client with connection pooling and error handling for distributed rate limiting.

This module provides a robust Redis client with connection pooling, circuit breaker pattern,
and comprehensive error handling for high-availability rate limiting operations.
"""

import asyncio
import time
import logging
from typing import Optional, Any, List, Dict
from contextlib import asynccontextmanager

import redis.asyncio as redis
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError, ConnectionError, TimeoutError

from .models import RedisConfig
from .config import config_manager

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker pattern for Redis operations."""
    
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed, open, half-open
    
    def can_execute(self) -> bool:
        """Check if operation can be executed."""
        if self.state == "closed":
            return True
        
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
                return True
            return False
        
        # half-open state
        return True
    
    def on_success(self):
        """Record successful operation."""
        self.failure_count = 0
        self.state = "closed"
    
    def on_failure(self):
        """Record failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")


class RedisClient:
    """High-availability Redis client for rate limiting operations."""
    
    def __init__(self, config: Optional[RedisConfig] = None):
        """
        Initialize Redis client with connection pooling.
        
        Args:
            config: Redis configuration. If None, uses configuration manager.
        """
        self.config = config or config_manager.config.redis
        self._pool: Optional[ConnectionPool] = None
        self._redis: Optional[Redis] = None
        self.circuit_breaker = CircuitBreaker()
        self._lock = asyncio.Lock()
        
        # Lua scripts for atomic operations
        self._rate_limit_script = """
            local key = KEYS[1]
            local window = tonumber(ARGV[1])
            local limit = tonumber(ARGV[2])
            local current_time = tonumber(ARGV[3])
            
            -- Calculate window start time
            local window_start = math.floor(current_time / window) * window
            local window_key = key .. ":" .. window_start
            
            -- Get current count
            local current = redis.call('GET', window_key)
            if current == false then
                current = 0
            else
                current = tonumber(current)
            end
            
            -- Check if limit exceeded
            if current >= limit then
                local ttl = redis.call('TTL', window_key)
                if ttl == -1 then
                    redis.call('EXPIRE', window_key, window)
                end
                return {0, current, window_start + window}
            end
            
            -- Increment counter and set expiration
            local new_count = redis.call('INCR', window_key)
            redis.call('EXPIRE', window_key, window + 1)  -- Extra second for safety
            
            return {1, new_count, window_start + window}
        """
        
        self._system_health_script = """
            local key = KEYS[1]
            local new_status = ARGV[1]
            local timestamp = ARGV[2]
            local ttl = tonumber(ARGV[3])
            
            -- Set the new status with timestamp
            redis.call('HSET', key, 'status', new_status, 'timestamp', timestamp)
            
            -- Set TTL for auto-recovery
            if ttl > 0 then
                redis.call('EXPIRE', key, ttl)
            end
            
            return redis.call('HGETALL', key)
        """
    
    async def _ensure_connection(self):
        """Ensure Redis connection is established."""
        if self._redis is None:
            async with self._lock:
                if self._redis is None:
                    await self._connect()
    
    async def _connect(self):
        """Establish Redis connection with pool."""
        logger.info(
            f"Establishing Redis connection to {self.config.host}:{self.config.port}",
            extra={
                "redis_host": self.config.host,
                "redis_port": self.config.port,
                "redis_db": self.config.db,
                "max_connections": self.config.max_connections,
                "timeout": self.config.timeout,
                "redis_operation": "connection_start"
            }
        )
        
        try:
            # Create connection pool
            self._pool = ConnectionPool(
                host=self.config.host,
                port=self.config.port,
                db=self.config.db,
                password=self.config.password,
                max_connections=self.config.max_connections,
                socket_timeout=self.config.timeout,
                socket_connect_timeout=self.config.timeout,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            logger.debug(
                f"Redis connection pool created with max_connections={self.config.max_connections}",
                extra={
                    "max_connections": self.config.max_connections,
                    "socket_timeout": self.config.timeout,
                    "health_check_interval": 30,
                    "redis_operation": "connection_pool_created"
                }
            )
            
            # Create Redis client
            self._redis = Redis(connection_pool=self._pool)
            
            # Test connection
            await self._redis.ping()
            
            logger.info(
                f"Redis connection established successfully to {self.config.host}:{self.config.port}",
                extra={
                    "redis_host": self.config.host,
                    "redis_port": self.config.port,
                    "redis_db": self.config.db,
                    "connection_status": "success",
                    "redis_operation": "connection_established"
                }
            )
            
        except Exception as e:
            logger.error(
                f"Failed to connect to Redis {self.config.host}:{self.config.port}: {type(e).__name__}: {e}",
                extra={
                    "redis_host": self.config.host,
                    "redis_port": self.config.port,
                    "redis_db": self.config.db,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "connection_status": "failed",
                    "redis_operation": "connection_failed"
                },
                exc_info=True
            )
            self._redis = None
            self._pool = None
            raise
    
    async def close(self):
        """Close Redis connection and pool."""
        if self._redis:
            await self._redis.close()
            self._redis = None
        
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        
        logger.info("Redis connection closed")
    
    async def is_healthy(self) -> bool:
        """Check if Redis connection is healthy."""
        try:
            if not self._redis:
                return False
            
            await self._redis.ping()
            return True
        except Exception:
            return False
    
    @asynccontextmanager
    async def _execute_with_circuit_breaker(self):
        """Execute Redis operation with circuit breaker pattern."""
        if not self.circuit_breaker.can_execute():
            raise ConnectionError("Circuit breaker is open")
        
        try:
            await self._ensure_connection()
            yield
            self.circuit_breaker.on_success()
        except Exception as e:
            self.circuit_breaker.on_failure()
            logger.error(f"Redis operation failed: {e}")
            raise
    
    async def check_rate_limit(self, user_id: str, limit: int, 
                              window_minutes: int) -> tuple[bool, int, int]:
        """
        Check rate limit for a user using sliding window algorithm.
        
        Args:
            user_id: User identifier
            limit: Request limit for the window
            window_minutes: Window size in minutes
            
        Returns:
            Tuple of (allowed, current_count, reset_timestamp)
        """
        logger.debug(
            f"Starting Redis rate limit check for user {user_id}",
            extra={
                "user_id": user_id,
                "limit": limit,
                "window_minutes": window_minutes,
                "redis_operation": "rate_limit_check_start"
            }
        )
        
        async with self._execute_with_circuit_breaker():
            key = f"rate_limit:user:{user_id}"
            window_seconds = window_minutes * 60
            current_time = int(time.time())
            window_start = (current_time // window_seconds) * window_seconds
            
            logger.debug(
                f"Rate limit parameters calculated for user {user_id}: "
                f"key={key}, window={window_seconds}s, current_time={current_time}, window_start={window_start}",
                extra={
                    "user_id": user_id,
                    "redis_key": key,
                    "window_seconds": window_seconds,
                    "current_time": current_time,
                    "window_start": window_start,
                    "limit": limit,
                    "redis_operation": "rate_limit_params_calculated"
                }
            )
            
            # Execute Lua script for atomic rate limiting
            try:
                result = await self._redis.eval(
                    self._rate_limit_script,
                    1,  # number of keys
                    key, window_seconds, limit, current_time
                )
                
                allowed = bool(result[0])
                current_count = int(result[1])
                reset_time = int(result[2])
                
                logger.info(
                    f"Redis rate limit check completed for user {user_id}: "
                    f"allowed={allowed}, count={current_count}/{limit}, reset_time={reset_time}",
                    extra={
                        "user_id": user_id,
                        "allowed": allowed,
                        "current_count": current_count,
                        "limit": limit,
                        "reset_time": reset_time,
                        "window_start": window_start,
                        "redis_key": key,
                        "redis_operation": "rate_limit_check_completed"
                    }
                )
                
                return allowed, current_count, reset_time
                
            except Exception as e:
                logger.error(
                    f"Redis rate limit script execution failed for user {user_id}: {type(e).__name__}: {e}",
                    extra={
                        "user_id": user_id,
                        "redis_key": key,
                        "limit": limit,
                        "window_seconds": window_seconds,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "redis_operation": "rate_limit_script_failed"
                    },
                    exc_info=True
                )
                raise
    
    async def get_user_rate_limit_status(self, user_id: str, 
                                       window_minutes: int) -> Dict[str, Any]:
        """
        Get current rate limit status for a user.
        
        Args:
            user_id: User identifier
            window_minutes: Window size in minutes
            
        Returns:
            Dictionary with current count and window information
        """
        async with self._execute_with_circuit_breaker():
            key = f"rate_limit:user:{user_id}"
            window_seconds = window_minutes * 60
            current_time = int(time.time())
            window_start = (current_time // window_seconds) * window_seconds
            window_key = f"{key}:{window_start}"
            
            current_count = await self._redis.get(window_key)
            ttl = await self._redis.ttl(window_key)
            
            return {
                "user_id": user_id,
                "current_count": int(current_count) if current_count else 0,
                "window_start": window_start,
                "window_end": window_start + window_seconds,
                "ttl": ttl if ttl > 0 else 0
            }
    
    async def set_system_health(self, status: str, ttl_seconds: Optional[int] = None,
                               updated_by: Optional[str] = None) -> Dict[str, str]:
        """
        Set system health status.
        
        Args:
            status: Health status (NORMAL or DEGRADED)
            ttl_seconds: Auto-reset TTL in seconds (None for no auto-reset)
            updated_by: Who updated the status
            
        Returns:
            Dictionary with updated health information
        """
        logger.info(
            f"Setting system health status to {status}",
            extra={
                "new_status": status,
                "ttl_seconds": ttl_seconds,
                "updated_by": updated_by,
                "redis_operation": "set_system_health_start"
            }
        )
        
        async with self._execute_with_circuit_breaker():
            key = "system:health"
            timestamp = str(int(time.time()))
            ttl = ttl_seconds or 0
            
            # Add updated_by to the hash if provided
            if updated_by:
                await self._redis.hset(key, "updated_by", updated_by)
            
            try:
                result = await self._redis.eval(
                    self._system_health_script,
                    1,  # number of keys
                    key, status, timestamp, ttl
                )
                
                # Convert result to dictionary
                health_data = {}
                for i in range(0, len(result), 2):
                    health_data[result[i].decode()] = result[i + 1].decode()
                
                logger.info(
                    f"System health status updated successfully to {status}",
                    extra={
                        "status": status,
                        "timestamp": timestamp,
                        "updated_by": updated_by,
                        "ttl_seconds": ttl_seconds,
                        "redis_key": key,
                        "redis_operation": "set_system_health_completed"
                    }
                )
                
                return health_data
                
            except Exception as e:
                logger.error(
                    f"Failed to set system health status: {type(e).__name__}: {e}",
                    extra={
                        "status": status,
                        "updated_by": updated_by,
                        "ttl_seconds": ttl_seconds,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "redis_operation": "set_system_health_failed"
                    },
                    exc_info=True
                )
                raise
    
    async def get_system_health(self) -> Dict[str, str]:
        """
        Get current system health status.
        
        Returns:
            Dictionary with health status and metadata
        """
        logger.debug(
            "Retrieving system health status from Redis",
            extra={
                "redis_operation": "get_system_health_start"
            }
        )
        
        try:
            async with self._execute_with_circuit_breaker():
                key = "system:health"
                result = await self._redis.hgetall(key)
                
                if not result:
                    # No health status set, default to NORMAL
                    default_health = {
                        "status": "NORMAL",
                        "timestamp": str(int(time.time())),
                        "updated_by": "system"
                    }
                    
                    logger.debug(
                        "No system health data found in Redis, using default NORMAL status",
                        extra={
                            "default_status": "NORMAL",
                            "redis_key": key,
                            "redis_operation": "get_system_health_default"
                        }
                    )
                    
                    return default_health
                
                # Convert bytes to strings
                health_data = {k.decode(): v.decode() for k, v in result.items()}
                
                logger.debug(
                    f"System health retrieved from Redis: {health_data.get('status')}",
                    extra={
                        "status": health_data.get('status'),
                        "timestamp": health_data.get('timestamp'),
                        "updated_by": health_data.get('updated_by'),
                        "redis_key": key,
                        "redis_operation": "get_system_health_retrieved"
                    }
                )
                
                return health_data
        
        except Exception as e:
            # Fallback to NORMAL status
            fallback_health = {
                "status": "NORMAL",
                "timestamp": str(int(time.time())),
                "updated_by": "fallback"
            }
            
            logger.error(
                f"Failed to get system health from Redis: {type(e).__name__}: {e}",
                extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "fallback_status": "NORMAL",
                    "redis_operation": "get_system_health_failed"
                },
                exc_info=True
            )
            
            return fallback_health
    
    async def increment_security_counter(self, key_suffix: str, 
                                       ttl_seconds: int = 300) -> int:
        """
        Increment security counter (for invalid API key attempts).
        
        Args:
            key_suffix: Suffix for the security key
            ttl_seconds: TTL for the counter
            
        Returns:
            Current counter value
        """
        async with self._execute_with_circuit_breaker():
            key = f"security:{key_suffix}"
            
            # Use pipeline for atomic operations
            async with self._redis.pipeline() as pipe:
                count = await pipe.incr(key).execute()
                
                # Set TTL only if this is the first increment
                if count[0] == 1:
                    await pipe.expire(key, ttl_seconds).execute()
                
                return count[0]
    
    async def is_ip_blocked(self, ip_address: str) -> bool:
        """
        Check if IP address is blocked.
        
        Args:
            ip_address: IP address to check
            
        Returns:
            True if IP is blocked, False otherwise
        """
        try:
            async with self._execute_with_circuit_breaker():
                key = f"security:blocked_ip:{ip_address}"
                return await self._redis.exists(key) > 0
        except Exception:
            # If Redis is down, don't block IPs (fail open)
            return False
    
    async def block_ip(self, ip_address: str, duration_seconds: int):
        """
        Block IP address for specified duration.
        
        Args:
            ip_address: IP address to block
            duration_seconds: Block duration in seconds
        """
        async with self._execute_with_circuit_breaker():
            key = f"security:blocked_ip:{ip_address}"
            await self._redis.setex(key, duration_seconds, "1")
            logger.warning(f"Blocked IP {ip_address} for {duration_seconds} seconds")


# Global Redis client instance
redis_client = RedisClient()
