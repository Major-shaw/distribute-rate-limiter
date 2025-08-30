"""
Core data models for the distributed rate limiter.

This module defines the Pydantic models used throughout the application
for configuration, rate limiting results, and system health management.
"""

from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Any
from pydantic import BaseModel, Field, validator


class SystemHealth(str, Enum):
    """System health states that affect rate limiting behavior."""
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"


class UserTier(str, Enum):
    """User tiers with different rate limiting policies."""
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TierConfig(BaseModel):
    """Configuration for a specific user tier."""
    base_limit: int = Field(..., gt=0, description="Standard request limit per window")
    burst_limit: int = Field(..., gt=0, description="Maximum burst limit during NORMAL state")
    degraded_limit: int = Field(..., gt=0, description="Reduced limit during DEGRADED state")
    window_minutes: int = Field(default=1, gt=0, description="Time window in minutes")
    
    @validator('burst_limit')
    def burst_must_be_gte_base(cls, v, values):
        """Burst limit must be greater than or equal to base limit."""
        if 'base_limit' in values and v < values['base_limit']:
            raise ValueError('burst_limit must be >= base_limit')
        return v
    
    @validator('degraded_limit')
    def degraded_must_be_positive(cls, v):
        """Degraded limit must be positive."""
        if v <= 0:
            raise ValueError('degraded_limit must be positive')
        return v


class RedisConfig(BaseModel):
    """Redis connection configuration."""
    host: str = Field(default="localhost", description="Redis host")
    port: int = Field(default=6379, ge=1, le=65535, description="Redis port")
    db: int = Field(default=0, ge=0, description="Redis database number")
    password: Optional[str] = Field(default=None, description="Redis password")
    timeout: float = Field(default=0.005, gt=0, description="Redis operation timeout in seconds")
    max_connections: int = Field(default=50, gt=0, description="Maximum Redis connections")


class RateLimitConfig(BaseModel):
    """Complete rate limiting configuration."""
    tiers: Dict[str, TierConfig] = Field(..., description="Tier configurations")
    users: Dict[str, str] = Field(..., description="User ID to tier mapping")
    api_keys: Dict[str, str] = Field(..., description="API key to user ID mapping")
    redis: RedisConfig = Field(default_factory=RedisConfig, description="Redis configuration")
    
    @validator('tiers')
    def tiers_must_contain_valid_tiers(cls, v):
        """Ensure all required tiers are present."""
        required_tiers = {tier.value for tier in UserTier}
        provided_tiers = set(v.keys())
        missing_tiers = required_tiers - provided_tiers
        if missing_tiers:
            raise ValueError(f"Missing required tiers: {missing_tiers}")
        return v
    
    @validator('users')
    def users_must_have_valid_tiers(cls, v, values):
        """Ensure all users reference valid tiers."""
        if 'tiers' not in values:
            return v
        
        valid_tiers = set(values['tiers'].keys())
        for user_id, tier in v.items():
            if tier not in valid_tiers:
                raise ValueError(f"User {user_id} references invalid tier: {tier}")
        return v
    
    @validator('api_keys')
    def api_keys_must_reference_valid_users(cls, v, values):
        """Ensure all API keys reference valid users."""
        if 'users' not in values:
            return v
        
        valid_users = set(values['users'].keys())
        for api_key, user_id in v.items():
            if user_id not in valid_users:
                raise ValueError(f"API key {api_key} references invalid user: {user_id}")
        return v


class RateLimitResult(BaseModel):
    """Result of a rate limit check."""
    allowed: bool = Field(..., description="Whether the request is allowed")
    remaining: int = Field(..., ge=0, description="Remaining requests in current window")
    reset_time: int = Field(..., description="Unix timestamp when the limit resets")
    limit: int = Field(..., gt=0, description="Current rate limit")
    user_id: str = Field(..., description="User ID for which the check was performed")
    tier: str = Field(..., description="User tier")


class APIKeyError(Exception):
    """Exception raised for API key validation errors."""
    
    def __init__(self, message: str, status_code: int, error_code: str):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(self.message)


class SecurityEvent(BaseModel):
    """Security event for logging invalid API key attempts."""
    event_type: str = Field(..., description="Type of security event")
    api_key_prefix: str = Field(..., description="First 8 characters of API key")
    ip_address: str = Field(..., description="Client IP address")
    user_agent: str = Field(..., description="Client User-Agent header")
    error_code: str = Field(..., description="Specific error code")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")
    request_id: Optional[str] = Field(default=None, description="Unique request identifier")


class SystemHealthStatus(BaseModel):
    """Current system health status with metadata."""
    status: SystemHealth = Field(..., description="Current system health state")
    last_updated: datetime = Field(..., description="When the status was last updated")
    updated_by: Optional[str] = Field(default=None, description="Who updated the status")
    reason: Optional[str] = Field(default=None, description="Reason for status change")
    auto_reset_at: Optional[datetime] = Field(default=None, description="When status will auto-reset")


class HealthCheckResponse(BaseModel):
    """Response model for health check endpoints."""
    status: str = Field(..., description="Service status")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Check timestamp")
    version: str = Field(default="1.0.0", description="Application version")
    components: Dict[str, str] = Field(default_factory=dict, description="Component statuses")
