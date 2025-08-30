"""
FastAPI middleware for distributed rate limiting.

This module implements the main rate limiting middleware that integrates
all components to provide comprehensive rate limiting functionality.
"""

import time
import uuid
import logging
from typing import Callable, Dict, Any
from datetime import datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.models import APIKeyError
from ..core.redis_client import redis_client
from ..services.user_service import api_key_validator, SecurityRateLimiter
from ..services.rate_limit_service import rate_limit_service

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for distributed rate limiting with dynamic health awareness.
    
    This middleware:
    1. Validates API keys and resolves user tiers
    2. Checks system health status
    3. Applies dynamic rate limiting based on tier and health
    4. Handles security rate limiting for invalid keys
    5. Returns appropriate HTTP responses with headers
    """
    
    def __init__(self, app, 
                 exclude_paths: list = None,
                 security_rate_limiter: SecurityRateLimiter = None):
        """
        Initialize rate limiting middleware.
        
        Args:
            app: FastAPI application instance
            exclude_paths: List of paths to exclude from rate limiting
            security_rate_limiter: Security rate limiter instance
        """
        super().__init__(app)
        self.exclude_paths = exclude_paths or [
            "/health",
            "/admin",
            "/docs", 
            "/redoc", 
            "/openapi.json"
        ]
        self.security_rate_limiter = security_rate_limiter or SecurityRateLimiter(redis_client)
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Main middleware processing logic.
        
        Args:
            request: FastAPI request object
            call_next: Next middleware/handler in chain
            
        Returns:
            HTTP response
        """
        # Generate unique request ID for tracing
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        start_time = time.time()
        
        # Log incoming request
        logger.info(
            f"[{request_id}] Incoming request: {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params),
                "user_agent": request.headers.get("User-Agent", ""),
                "content_type": request.headers.get("Content-Type", ""),
                "lifecycle_stage": "request_start"
            }
        )
        
        # Skip rate limiting for excluded paths
        if self._should_exclude_path(request.url.path):
            logger.debug(
                f"[{request_id}] Skipping rate limiting for excluded path: {request.url.path}",
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "lifecycle_stage": "path_excluded"
                }
            )
            return await call_next(request)
        
        try:
            # Extract client information
            client_ip = self._get_client_ip(request)
            user_agent = request.headers.get("User-Agent", "")
            
            # Create request context for logging
            request_context = {
                "ip_address": client_ip,
                "user_agent": user_agent,
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method
            }
            
            logger.debug(
                f"[{request_id}] Client info extracted - IP: {client_ip}",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "user_agent": user_agent,
                    "lifecycle_stage": "client_info_extracted"
                }
            )
            
            # Check if IP is blocked for security violations
            if await self.security_rate_limiter.is_ip_blocked(client_ip):
                logger.warning(
                    f"[{request_id}] Blocked IP attempted access: {client_ip}",
                    extra={
                        "request_id": request_id,
                        "client_ip": client_ip,
                        "lifecycle_stage": "ip_blocked"
                    }
                )
                return self._create_blocked_ip_response(request_id)
            
            # Validate API key and get user information
            api_key = request.headers.get("X-API-Key")
            api_key_preview = api_key[:10] + "..." if api_key and len(api_key) > 10 else "None"
            
            logger.debug(
                f"[{request_id}] Validating API key: {api_key_preview}",
                extra={
                    "request_id": request_id,
                    "api_key_preview": api_key_preview,
                    "has_api_key": bool(api_key),
                    "lifecycle_stage": "api_key_validation_start"
                }
            )
            
            try:
                user_id, tier = api_key_validator.validate_api_key(api_key, request_context)
                logger.info(
                    f"[{request_id}] API key validated successfully - User: {user_id}, Tier: {tier}",
                    extra={
                        "request_id": request_id,
                        "user_id": user_id,
                        "tier": tier,
                        "api_key_preview": api_key_preview,
                        "lifecycle_stage": "api_key_validated"
                    }
                )
            except APIKeyError as e:
                logger.warning(
                    f"[{request_id}] API key validation failed: {e.message}",
                    extra={
                        "request_id": request_id,
                        "api_key_preview": api_key_preview,
                        "error_code": e.error_code,
                        "error_message": e.message,
                        "client_ip": client_ip,
                        "lifecycle_stage": "api_key_validation_failed"
                    }
                )
                return await self._handle_invalid_api_key(e, client_ip, request_context)
            
            # Perform rate limiting check
            logger.debug(
                f"[{request_id}] Starting rate limit check for user {user_id} (tier: {tier})",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "tier": tier,
                    "lifecycle_stage": "rate_limit_check_start"
                }
            )
            
            rate_limit_result = await rate_limit_service.check_rate_limit(user_id, tier)
            
            logger.debug(
                f"[{request_id}] Rate limit check completed - Allowed: {rate_limit_result.allowed}, "
                f"Remaining: {rate_limit_result.remaining}/{rate_limit_result.limit}",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "tier": tier,
                    "allowed": rate_limit_result.allowed,
                    "remaining": rate_limit_result.remaining,
                    "limit": rate_limit_result.limit,
                    "reset_time": rate_limit_result.reset_time,
                    "lifecycle_stage": "rate_limit_check_completed"
                }
            )
            
            if not rate_limit_result.allowed:
                # Rate limit exceeded
                logger.warning(
                    f"[{request_id}] Rate limit exceeded for user {user_id} (tier: {tier}) - "
                    f"Limit: {rate_limit_result.limit}, Reset in: {rate_limit_result.reset_time - int(time.time())}s",
                    extra={
                        "request_id": request_id,
                        "rate_limit_violation": True,
                        "user_id": user_id,
                        "tier": tier,
                        "limit": rate_limit_result.limit,
                        "reset_time": rate_limit_result.reset_time,
                        "client_ip": client_ip,
                        "lifecycle_stage": "rate_limit_exceeded"
                    }
                )
                
                response = self._create_rate_limited_response(rate_limit_result, request_id)
                return response
            
            # Rate limit check passed, process the request
            logger.debug(
                f"[{request_id}] Rate limit check passed, forwarding request to handler",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "tier": tier,
                    "remaining": rate_limit_result.remaining,
                    "lifecycle_stage": "forwarding_to_handler"
                }
            )
            
            # Store rate limit info in request state for endpoints to access
            request.state.rate_limit_result = rate_limit_result
            request.state.user_id = user_id
            request.state.tier = tier
            
            # Process the request
            response = await call_next(request)
            
            # Add rate limiting headers to successful responses
            self._add_rate_limit_headers(response, rate_limit_result)
            
            # Add request ID header
            response.headers["X-Request-ID"] = request_id
            
            # Log successful request completion
            duration = time.time() - start_time
            logger.info(
                f"[{request_id}] Request completed successfully for user {user_id} (tier: {tier}) - "
                f"Duration: {duration:.3f}s, Status: {response.status_code}, Remaining: {rate_limit_result.remaining}",
                extra={
                    "request_id": request_id,
                    "user_id": user_id,
                    "tier": tier,
                    "remaining": rate_limit_result.remaining,
                    "duration": duration,
                    "status_code": response.status_code,
                    "response_size": len(response.body) if hasattr(response, 'body') else 0,
                    "lifecycle_stage": "request_completed"
                }
            )
            
            return response
            
        except Exception as e:
            # Handle unexpected errors
            duration = time.time() - start_time
            logger.error(
                f"[{request_id}] Unexpected error in rate limiting middleware: {type(e).__name__}: {e}",
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "method": request.method,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "duration": duration,
                    "lifecycle_stage": "middleware_error"
                },
                exc_info=True
            )
            
            # Return 500 error
            return JSONResponse(
                content={
                    "error": "Internal server error",
                    "message": "An unexpected error occurred while processing your request",
                    "request_id": request_id
                },
                status_code=500,
                headers={"X-Request-ID": request_id}
            )
    
    def _should_exclude_path(self, path: str) -> bool:
        """
        Check if path should be excluded from rate limiting.
        
        Args:
            path: Request path
            
        Returns:
            True if path should be excluded
        """
        # Normalize path (remove trailing slash except for root)
        normalized_path = path.rstrip('/') or '/'
        
        for excluded in self.exclude_paths:
            # Normalize excluded path
            normalized_excluded = excluded.rstrip('/') or '/'
            
            # Exact match
            if normalized_path == normalized_excluded:
                return True
                
            # Prefix match for directories (excluded path ends with /*)
            if excluded.endswith('/*'):
                prefix = excluded[:-2]  # Remove /*
                normalized_prefix = prefix.rstrip('/') or '/'
                if normalized_path.startswith(normalized_prefix + '/') or normalized_path == normalized_prefix:
                    return True
        
        return False
    
    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP address from request.
        
        Args:
            request: FastAPI request object
            
        Returns:
            Client IP address
        """
        # Check for forwarded headers (when behind a proxy/load balancer)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For can contain multiple IPs, take the first one
            return forwarded_for.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fallback to direct client IP
        return request.client.host if request.client else "unknown"
    
    async def _handle_invalid_api_key(self, error: APIKeyError, client_ip: str,
                                     request_context: Dict[str, Any]) -> JSONResponse:
        """
        Handle invalid API key with security rate limiting.
        
        Args:
            error: API key error
            client_ip: Client IP address
            request_context: Request context
            
        Returns:
            JSON response for invalid API key
        """
        request_id = request_context.get("request_id")
        
        logger.debug(
            f"[{request_id}] Checking security rate limit for invalid API key attempt from IP: {client_ip}",
            extra={
                "request_id": request_id,
                "client_ip": client_ip,
                "error_code": error.error_code,
                "lifecycle_stage": "security_rate_limit_check"
            }
        )
        
        # Check rate limit for invalid key attempts
        if not await self.security_rate_limiter.check_invalid_key_attempts(client_ip):
            # Too many invalid attempts, block the IP
            logger.warning(
                f"[{request_id}] IP {client_ip} blocked due to too many invalid API key attempts",
                extra={
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "action": "ip_blocked",
                    "block_duration_minutes": 15,
                    "lifecycle_stage": "ip_blocked_security"
                }
            )
            
            await self.security_rate_limiter.block_ip(client_ip, duration_minutes=15)
            
            return JSONResponse(
                content={
                    "error": "Too many invalid API key attempts. IP temporarily blocked.",
                    "error_code": "IP_BLOCKED",
                    "request_id": request_id,
                    "retry_after": 900
                },
                status_code=429,
                headers={
                    "X-Request-ID": request_id or "",
                    "Retry-After": "900"  # 15 minutes
                }
            )
        
        # Return the original API key error
        logger.info(
            f"[{request_id}] Returning API key error: {error.error_code} - {error.message}",
            extra={
                "request_id": request_id,
                "client_ip": client_ip,
                "error_code": error.error_code,
                "status_code": error.status_code,
                "lifecycle_stage": "api_key_error_response"
            }
        )
        
        return JSONResponse(
            content={
                "error": error.message,
                "error_code": error.error_code,
                "timestamp": datetime.utcnow().isoformat(),
                "request_id": request_id
            },
            status_code=error.status_code,
            headers={"X-Request-ID": request_id or ""}
        )
    
    def _create_blocked_ip_response(self, request_id: str) -> JSONResponse:
        """
        Create response for blocked IP addresses.
        
        Args:
            request_id: Request identifier
            
        Returns:
            JSON response for blocked IP
        """
        return JSONResponse(
            content={
                "error": "IP address is temporarily blocked due to abuse.",
                "error_code": "IP_BLOCKED",
                "request_id": request_id
            },
            status_code=429,
            headers={
                "X-Request-ID": request_id,
                "Retry-After": "900"  # 15 minutes
            }
        )
    
    def _create_rate_limited_response(self, rate_limit_result, request_id: str) -> JSONResponse:
        """
        Create response for rate limited requests.
        
        Args:
            rate_limit_result: Rate limit result object
            request_id: Request identifier
            
        Returns:
            JSON response for rate limited request
        """
        retry_after = rate_limit_result.reset_time - int(time.time())
        retry_after = max(1, retry_after)  # At least 1 second
        
        return JSONResponse(
            content={
                "error": "Rate limit exceeded",
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": f"You have exceeded the rate limit of {rate_limit_result.limit} requests per minute.",
                "retry_after": retry_after,
                "request_id": request_id
            },
            status_code=429,
            headers={
                "X-Request-ID": request_id,
                "X-RateLimit-Limit": str(rate_limit_result.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(rate_limit_result.reset_time),
                "Retry-After": str(retry_after)
            }
        )
    
    def _add_rate_limit_headers(self, response: Response, rate_limit_result) -> None:
        """
        Add rate limiting headers to successful responses.
        
        Args:
            response: HTTP response object
            rate_limit_result: Rate limit result object
        """
        response.headers["X-RateLimit-Limit"] = str(rate_limit_result.limit)
        response.headers["X-RateLimit-Remaining"] = str(rate_limit_result.remaining)
        response.headers["X-RateLimit-Reset"] = str(rate_limit_result.reset_time)


def create_rate_limit_middleware(exclude_paths: list = None) -> RateLimitMiddleware:
    """
    Factory function to create rate limiting middleware.
    
    Args:
        exclude_paths: List of paths to exclude from rate limiting
        
    Returns:
        Configured rate limiting middleware
    """
    def middleware_factory(app):
        return RateLimitMiddleware(app, exclude_paths=exclude_paths)
    
    return middleware_factory
