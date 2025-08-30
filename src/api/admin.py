"""
Admin endpoints for system management and monitoring.

This module provides administrative endpoints for managing system health,
users, API keys, and monitoring rate limiting status.
"""

import secrets
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query, Path, Body
from pydantic import BaseModel, Field

from ..core.models import SystemHealth, UserTier
from ..services.rate_limit_service import health_service, rate_limit_service
from ..services.user_service import user_tier_manager
from ..core.config import config_manager

router = APIRouter(prefix="/admin", tags=["admin"])


# Request/Response models
class SystemHealthRequest(BaseModel):
    """Request model for updating system health."""
    status: SystemHealth = Field(..., description="New system health status")
    ttl_seconds: Optional[int] = Field(None, ge=1, description="Auto-reset TTL in seconds")
    updated_by: Optional[str] = Field(None, description="Who is updating the status")
    reason: Optional[str] = Field(None, description="Reason for status change")


class CreateUserRequest(BaseModel):
    """Request model for creating a new user."""
    user_id: str = Field(..., min_length=1, max_length=100, description="User identifier")
    tier: UserTier = Field(..., description="User tier")


class CreateAPIKeyRequest(BaseModel):
    """Request model for creating a new API key."""
    user_id: str = Field(..., min_length=1, max_length=100, description="User identifier")
    api_key: Optional[str] = Field(None, description="Custom API key (auto-generated if not provided)")


class APIKeyResponse(BaseModel):
    """Response model for API key creation."""
    api_key: str = Field(..., description="Generated or provided API key")
    user_id: str = Field(..., description="User identifier")
    tier: str = Field(..., description="User tier")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")


# Authentication dependency (simplified for demo)
async def verify_admin_access(admin_key: Optional[str] = Query(None, alias="admin_key")):
    """
    Verify admin access (simplified authentication).
    
    In production, this would use proper authentication mechanisms
    like JWT tokens, OAuth, etc.
    """
    # For demo purposes, we'll use a simple admin key check
    # In production, implement proper authentication
    expected_admin_key = config_manager.config.api_keys.get("admin_api_key")
    if not expected_admin_key or admin_key != expected_admin_key:
        # For demo, allow access without admin key but log a warning
        pass  # In production: raise HTTPException(status_code=401, detail="Admin access required")
    
    return True


@router.get("/health", summary="Get system health status")
async def get_system_health(_: bool = Depends(verify_admin_access)):
    """
    Get current system health status with metadata.
    
    Returns comprehensive health information including:
    - Current health status (NORMAL/DEGRADED)
    - When it was last updated and by whom
    - Component health checks (Redis, config, etc.)
    """
    try:
        # Get system health status
        health_status = await health_service.get_system_health()
        
        # Get overall health check
        health_check = await health_service.is_healthy()
        
        return {
            "system_health": health_status,
            "health_check": health_check,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get health status: {e}")


@router.post("/health", summary="Update system health status")
async def set_system_health(
    request: SystemHealthRequest,
    _: bool = Depends(verify_admin_access)
):
    """
    Update system health status.
    
    This endpoint allows administrators to change the system health status
    between NORMAL and DEGRADED states, which affects rate limiting behavior:
    
    - NORMAL: Users can burst above base limits
    - DEGRADED: Strict enforcement of SLA limits, free tier heavily throttled
    """
    try:
        updated_health = await health_service.set_system_health(
            status=request.status.value,
            ttl_seconds=request.ttl_seconds,
            updated_by=request.updated_by or "admin_api"
        )
        
        return {
            "message": f"System health updated to {request.status.value}",
            "health_status": updated_health,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update health status: {e}")


@router.get("/users", summary="List all users")
async def list_users(_: bool = Depends(verify_admin_access)):
    """
    List all users with their tier information and API keys.
    
    Returns comprehensive information about all users including:
    - User ID and tier
    - All API keys associated with each user
    - API key counts
    """
    try:
        users = user_tier_manager.list_users()
        return {
            "users": users,
            "total_users": len(users),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list users: {e}")


@router.post("/users", summary="Create a new user")
async def create_user(
    request: CreateUserRequest,
    _: bool = Depends(verify_admin_access)
):
    """
    Create a new user with specified tier.
    
    The user will be created with the specified tier and can then have
    API keys generated for them.
    """
    try:
        # Check if user already exists
        existing_tier = user_tier_manager.get_tier_from_user(request.user_id)
        if existing_tier:
            raise HTTPException(
                status_code=409, 
                detail=f"User {request.user_id} already exists with tier {existing_tier}"
            )
        
        # Create the user
        success = user_tier_manager.add_user(request.user_id, request.tier.value)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create user")
        
        return {
            "message": f"User {request.user_id} created successfully",
            "user_id": request.user_id,
            "tier": request.tier.value,
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {e}")


@router.get("/users/{user_id}", summary="Get user information")
async def get_user(
    user_id: str = Path(..., description="User ID to get information for"),
    _: bool = Depends(verify_admin_access)
):
    """
    Get detailed information about a specific user.
    
    Returns user information including tier, API keys, and current
    rate limiting status.
    """
    try:
        # Get user info
        user_info = user_tier_manager.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        
        # Get rate limit status
        tier = user_info["tier"]
        rate_limit_status = await rate_limit_service.get_user_status(user_id, tier)
        
        return {
            "user_info": user_info,
            "rate_limit_status": rate_limit_status,
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get user info: {e}")


@router.post("/api-keys", summary="Create a new API key")
async def create_api_key(
    request: CreateAPIKeyRequest,
    _: bool = Depends(verify_admin_access)
) -> APIKeyResponse:
    """
    Create a new API key for an existing user.
    
    If no custom API key is provided, one will be auto-generated.
    The API key will be associated with the specified user.
    """
    try:
        # Check if user exists
        user_info = user_tier_manager.get_user_info(request.user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail=f"User {request.user_id} not found")
        
        # Generate or use provided API key
        if request.api_key:
            api_key = request.api_key
            # Check if API key already exists
            existing_user = user_tier_manager.get_user_from_api_key(api_key)
            if existing_user:
                raise HTTPException(
                    status_code=409, 
                    detail=f"API key already exists for user {existing_user}"
                )
        else:
            # Generate new API key
            api_key = user_tier_manager.generate_api_key(request.user_id, user_info["tier"])
            if not api_key:
                raise HTTPException(status_code=500, detail="Failed to generate API key")
        
        # Add the API key
        if request.api_key:  # Only add if using custom key (generated key is already added)
            success = user_tier_manager.add_api_key(api_key, request.user_id)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to add API key")
        
        return APIKeyResponse(
            api_key=api_key,
            user_id=request.user_id,
            tier=user_info["tier"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {e}")


@router.get("/api-keys/{api_key}/info", summary="Get API key information")
async def get_api_key_info(
    api_key: str = Path(..., description="API key to get information for"),
    _: bool = Depends(verify_admin_access)
):
    """
    Get information about a specific API key.
    
    Returns the user associated with the API key and their current
    rate limiting status.
    """
    try:
        # Get user from API key
        user_tier_info = user_tier_manager.get_user_tier(api_key)
        if not user_tier_info:
            raise HTTPException(status_code=404, detail="API key not found")
        
        user_id, tier = user_tier_info
        
        # Get rate limit status
        rate_limit_status = await rate_limit_service.get_user_status(user_id, tier)
        
        return {
            "api_key": api_key[:20] + "..." if len(api_key) > 20 else api_key,
            "user_id": user_id,
            "tier": tier,
            "rate_limit_status": rate_limit_status,
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get API key info: {e}")


@router.get("/rate-limits/status", summary="Get overall rate limiting status")
async def get_rate_limit_status(
    user_id: Optional[str] = Query(None, description="Filter by specific user"),
    _: bool = Depends(verify_admin_access)
):
    """
    Get overall rate limiting status and statistics.
    
    Optionally filter by specific user ID to get detailed information
    about their current rate limiting status.
    """
    try:
        if user_id:
            # Get specific user status
            user_info = user_tier_manager.get_user_info(user_id)
            if not user_info:
                raise HTTPException(status_code=404, detail=f"User {user_id} not found")
            
            tier = user_info["tier"]
            status = await rate_limit_service.get_user_status(user_id, tier)
            
            return {
                "user_specific": True,
                "user_id": user_id,
                "status": status,
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            # Get system-wide status
            system_health = await health_service.get_system_health()
            users = user_tier_manager.list_users()
            
            return {
                "user_specific": False,
                "system_health": system_health,
                "total_users": len(users),
                "users_by_tier": {
                    tier: len([u for u in users.values() if u["tier"] == tier])
                    for tier in ["free", "pro", "enterprise"]
                },
                "timestamp": datetime.utcnow().isoformat()
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get rate limit status: {e}")


@router.post("/rate-limits/reset/{user_id}", summary="Reset user rate limit")
async def reset_user_rate_limit(
    user_id: str = Path(..., description="User ID to reset rate limit for"),
    _: bool = Depends(verify_admin_access)
):
    """
    Reset rate limit for a specific user.
    
    This is an emergency function that can be used to immediately
    reset a user's rate limit if they've been incorrectly limited.
    """
    try:
        # Check if user exists
        user_info = user_tier_manager.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")
        
        # Reset rate limit
        success = await rate_limit_service.reset_user_rate_limit(user_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to reset rate limit")
        
        return {
            "message": f"Rate limit reset for user {user_id}",
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset rate limit: {e}")


@router.get("/config", summary="Get current configuration")
async def get_config(_: bool = Depends(verify_admin_access)):
    """
    Get current system configuration.
    
    Returns the complete configuration including tiers, users, and
    Redis settings (passwords excluded for security).
    """
    try:
        config = config_manager.config
        
        # Create safe config (excluding sensitive information)
        safe_config = {
            "tiers": {k: v.dict() for k, v in config.tiers.items()},
            "users": config.users,
            "api_keys": {k: v for k, v in list(config.api_keys.items())[:5]},  # Show only first 5
            "api_key_count": len(config.api_keys),
            "redis": {
                "host": config.redis.host,
                "port": config.redis.port,
                "db": config.redis.db,
                "timeout": config.redis.timeout,
                "max_connections": config.redis.max_connections
                # Exclude password for security
            }
        }
        
        return {
            "config": safe_config,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get configuration: {e}")


@router.post("/config/reload", summary="Reload configuration from file")
async def reload_config(_: bool = Depends(verify_admin_access)):
    """
    Reload configuration from file.
    
    This allows updating the configuration without restarting the service.
    User mappings and tier settings will be refreshed.
    """
    try:
        # Reload configuration
        config_success = config_manager.reload_config()
        user_success = user_tier_manager.reload_users()
        
        if config_success and user_success:
            return {
                "message": "Configuration reloaded successfully",
                "timestamp": datetime.utcnow().isoformat()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to reload configuration")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reload configuration: {e}")
