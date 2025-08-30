"""
Test endpoints for demonstrating rate limiting functionality.

This module provides simple endpoints to test and demonstrate
the rate limiting behavior in different scenarios.
"""

import time
import json
from typing import Optional, Dict, Any
from datetime import datetime
import logging
from fastapi import APIRouter, Request, Query, Path, HTTPException, Depends
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from ..services.rate_limit_service import health_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["test"])

# API key security scheme for documentation
api_key_header = APIKeyHeader(name="X-API-Key", description="API key for authentication")

async def get_api_key(api_key: str = Depends(api_key_header)) -> str:
    """
    Dependency to extract API key for documentation purposes.
    
    Note: Actual API key validation is handled by middleware.
    This dependency is primarily for OpenAPI documentation.
    """
    return api_key


def get_rate_limit_info_from_state(request: Request) -> Optional[Dict[str, Any]]:
    """Extract rate limit information from request state (set by middleware)."""
    rate_limit_result = getattr(request.state, "rate_limit_result", None)
    if rate_limit_result:
        return {
            "limit": rate_limit_result.limit,
            "remaining": rate_limit_result.remaining,
            "reset": rate_limit_result.reset_time
        }
    return {
        "limit": None,
        "remaining": None,
        "reset": None
    }


def get_user_info_from_state(request: Request) -> Optional[Dict[str, Any]]:
    """Extract user information from request state (set by middleware)."""
    user_id = getattr(request.state, "user_id", None)
    tier = getattr(request.state, "tier", None)
    if user_id and tier:
        return {
            "user_id": user_id,
            "tier": tier
        }
    return None


class TestResponse(BaseModel):
    """Standard response model for test endpoints."""
    message: str
    timestamp: datetime
    request_id: Optional[str] = None
    user_info: Optional[Dict[str, Any]] = None
    rate_limit_info: Optional[Dict[str, Any]] = None


@router.get("/test", summary="Basic rate limiting test endpoint")
async def basic_test(request: Request, api_key: str = Depends(get_api_key)) -> TestResponse:
    """
    Basic test endpoint for rate limiting.
    
    This endpoint requires a valid API key and will be rate limited
    based on the user's tier and current system health status.
    
    Use this endpoint to test:
    - Basic rate limiting functionality
    - Different API keys and tiers
    - System health state changes
    """
    # Get information from request state (set by middleware)
    request_id = getattr(request.state, "request_id", None)
    rate_limit_info = get_rate_limit_info_from_state(request)
    user_info = get_user_info_from_state(request)
    
    return TestResponse(
        message="Hello! Your request was processed successfully.",
        timestamp=datetime.utcnow(),
        request_id=request_id,
        user_info=user_info,
        rate_limit_info=rate_limit_info
    )


@router.get("/test/simulate-load", summary="Simulate API load for testing")
async def simulate_load(
    request: Request,
    requests: int = Query(5, ge=1, le=50, description="Number of requests to simulate"),
    delay_ms: int = Query(100, ge=0, le=5000, description="Delay between requests in milliseconds"),
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Simulate multiple API requests for load testing.
    
    This endpoint makes multiple internal calls to test rate limiting
    behavior under load. Use this to:
    - Test burst behavior
    - Verify rate limit enforcement
    - Observe rate limiting in action
    
    Note: This endpoint itself is rate limited, so you may hit limits
    before completing all simulated requests.
    """
    results = []
    start_time = time.time()
    
    for i in range(requests):
        request_start = time.time()
        
        try:
            # Simulate request processing
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            
            # Get current rate limit info from request state
            rate_limit_info = get_rate_limit_info_from_state(request)
            
            results.append({
                "request_number": i + 1,
                "success": True,
                "duration_ms": round((time.time() - request_start) * 1000, 2),
                "rate_limit_info": rate_limit_info
            })
            
        except Exception as e:
            results.append({
                "request_number": i + 1,
                "success": False,
                "error": str(e),
                "duration_ms": round((time.time() - request_start) * 1000, 2)
            })
    
    total_duration = time.time() - start_time
    successful_requests = sum(1 for r in results if r["success"])
    
    return {
        "summary": {
            "total_requests": requests,
            "successful_requests": successful_requests,
            "failed_requests": requests - successful_requests,
            "total_duration_ms": round(total_duration * 1000, 2),
            "average_duration_ms": round((total_duration * 1000) / requests, 2)
        },
        "results": results,
        "timestamp": datetime.utcnow().isoformat(),
        "request_id": getattr(request.state, "request_id", None)
    }


@router.get("/test/tier-demo/{tier}", summary="Demonstrate tier-specific behavior")
async def tier_demo(
    request: Request,
    tier: str = Path(..., regex="^(free|pro|enterprise)$", description="Tier to demonstrate"),
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Demonstrate rate limiting behavior for different tiers.
    
    This endpoint shows how different tiers behave under various
    system health conditions. Use this to understand:
    - Tier-specific limits
    - Burst behavior differences
    - Degraded state impact
    """
    # Get system health
    health_status = await health_service.get_system_health()
    
    # Get rate limit info from request state
    rate_limit_info = get_rate_limit_info_from_state(request)
    
    # Provide tier-specific guidance
    tier_guidance = {
        "free": {
            "normal_state": "Can burst up to 20 RPM (from base 10 RPM)",
            "degraded_state": "Heavily throttled to 2 RPM",
            "recommendation": "Upgrade to Pro for better limits"
        },
        "pro": {
            "normal_state": "Can burst up to 150 RPM (from base 100 RPM)", 
            "degraded_state": "Limited to base 100 RPM (no bursting)",
            "recommendation": "SLA protected during degraded states"
        },
        "enterprise": {
            "normal_state": "Consistent 1000 RPM limit",
            "degraded_state": "Full 1000 RPM maintained (SLA protected)",
            "recommendation": "Highest tier with full SLA protection"
        }
    }
    
    return {
        "tier": tier,
        "system_health": health_status.get("status", "UNKNOWN"),
        "current_rate_limits": rate_limit_info,
        "tier_behavior": tier_guidance.get(tier, {}),
        "message": f"This demonstrates {tier} tier behavior under {health_status.get('status', 'UNKNOWN')} system conditions.",
        "timestamp": datetime.utcnow().isoformat(),
        "request_id": getattr(request.state, "request_id", None)
    }


@router.get("/test/health-impact", summary="Show system health impact on rate limits")
async def health_impact(request: Request, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
    """
    Demonstrate how system health affects rate limiting.
    
    This endpoint shows the current system health status and
    explains how it affects rate limiting for different tiers.
    """
    # Get system health
    health_status = await health_service.get_system_health()
    current_health = health_status.get("status", "NORMAL")
    
    # Get rate limit info from request state
    rate_limit_info = get_rate_limit_info_from_state(request)
    
    # Explain health impact
    health_impact_explanation = {
        "NORMAL": {
            "description": "System is operating normally",
            "rate_limiting": "Users can burst above their base limits",
            "free_tier": "10 RPM base → 20 RPM burst allowed",
            "pro_tier": "100 RPM base → 150 RPM burst allowed", 
            "enterprise_tier": "1000 RPM maintained"
        },
        "DEGRADED": {
            "description": "System is under heavy load",
            "rate_limiting": "Strict SLA enforcement, load shedding for free users",
            "free_tier": "Heavily throttled to 2 RPM (load shedding)",
            "pro_tier": "Limited to base 100 RPM (SLA protected)",
            "enterprise_tier": "Full 1000 RPM maintained (SLA protected)"
        }
    }
    
    return {
        "current_health": current_health,
        "health_metadata": health_status,
        "rate_limit_info": rate_limit_info,
        "health_impact": health_impact_explanation.get(current_health, {}),
        "message": f"System is currently in {current_health} state",
        "timestamp": datetime.utcnow().isoformat(),
        "request_id": getattr(request.state, "request_id", None)
    }


@router.get("/test/burst", summary="Test burst rate limiting behavior")
async def test_burst(
    request: Request,
    rapid_requests: int = Query(15, ge=5, le=30, description="Number of rapid requests to attempt"),
    api_key: str = Depends(get_api_key)
) -> Dict[str, Any]:
    """
    Test burst rate limiting behavior.
    
    This endpoint attempts to make rapid requests to test:
    - Burst limits during NORMAL state
    - How quickly limits are enforced
    - Recovery behavior
    
    Note: This endpoint makes rapid calls and may trigger rate limits.
    """
    results = []
    start_time = time.time()
    
    for i in range(rapid_requests):
        request_time = time.time()
        
        # Get current rate limit status from request state
        rate_limit_info = get_rate_limit_info_from_state(request)
        rate_limit_info["timestamp"] = request_time
        
        results.append({
            "request_number": i + 1,
            "elapsed_ms": round((request_time - start_time) * 1000, 2),
            "rate_limit_info": rate_limit_info
        })
        
        # Small delay to avoid overwhelming the system
        time.sleep(0.01)  # 10ms delay
    
    # Analyze the results
    rate_limited = any(
        r["rate_limit_info"].get("remaining") == "0" 
        for r in results
    )
    
    return {
        "test_summary": {
            "rapid_requests_attempted": rapid_requests,
            "rate_limit_triggered": rate_limited,
            "total_duration_ms": round((time.time() - start_time) * 1000, 2)
        },
        "request_details": results,
        "analysis": {
            "burst_behavior": "NORMAL state allows bursting" if not rate_limited else "Rate limit enforced",
            "recommendation": "Try changing system health to see different behavior"
        },
        "timestamp": datetime.utcnow().isoformat(),
        "request_id": getattr(request.state, "request_id", None)
    }


@router.get("/health", summary="Application health check")
async def health_check() -> Dict[str, Any]:
    """
    Simple health check endpoint (excluded from rate limiting).
    
    This endpoint provides basic health information about the service
    and is excluded from rate limiting for monitoring purposes.
    """
    health_check_result = await health_service.is_healthy()
    
    return {
        "status": "healthy" if health_check_result["overall_status"] == "healthy" else "unhealthy",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "components": health_check_result["components"],
        "message": "Rate limiter service is operational"
    }


@router.get("/", summary="API information")
async def api_info() -> Dict[str, Any]:
    """
    Basic API information endpoint (excluded from rate limiting).
    
    Provides information about the rate limiting API and how to use it.
    """
    return {
        "name": "Distributed Rate Limiter API",
        "version": "1.0.0",
        "description": "Dynamic, load-aware rate limiting for FastAPI",
        "features": [
            "Tier-based rate limiting (Free, Pro, Enterprise)",
            "Dynamic limits based on system health",
            "Distributed counting with Redis",
            "Security rate limiting for invalid API keys",
            "Comprehensive admin endpoints"
        ],
        "usage": {
            "authentication": "Include X-API-Key header with your API key",
            "rate_limits": "Vary by tier and system health status",
            "headers": "Check X-RateLimit-* headers for limit information"
        },
        "endpoints": {
            "test": "/test - Basic rate limiting test",
            "admin": "/admin/* - Administrative endpoints",
            "health": "/health - Service health check",
            "docs": "/docs - Interactive API documentation"
        },
        "timestamp": datetime.utcnow().isoformat()
    }
