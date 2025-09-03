"""
Main FastAPI application with distributed rate limiting.

This is the entry point for the rate limiting service that demonstrates
dynamic, load-aware rate limiting with Redis-based distributed storage.
"""

import logging
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from src.middleware.rate_limiter import RateLimitMiddleware
from src.api.admin import router as admin_router
from src.api.test_endpoints import router as test_router
from src.core.redis_client import redis_client
from src.core.config import config_manager
from src.core.logging_config import setup_logging_from_env

# Setup logging using environment variables or defaults
setup_logging_from_env()
logger = logging.getLogger(__name__)

# Define API key security scheme
api_key_header = APIKeyHeader(name="X-API-Key", description="API key for authentication")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown events.
    """
    # Startup
    logger.info("Starting up Rate Limiter service...")
    
    try:
        # Initialize Redis connection
        await redis_client._ensure_connection()
        logger.info("Redis connection established")
        
        # Load configuration
        config = config_manager.config
        logger.info(f"Configuration loaded with {len(config.users)} users and {len(config.api_keys)} API keys")
        
        # Set initial system health to NORMAL
        try:
            from src.services.rate_limit_service import health_service
            await health_service.set_system_health("NORMAL", updated_by="system_startup")
            logger.info("System health initialized to NORMAL")
        except Exception as e:
            logger.warning(f"Could not set initial system health: {e}")
        
        logger.info("Rate Limiter service startup completed successfully")
        
    except Exception as e:
        logger.error(f"Failed to start Rate Limiter service: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down Rate Limiter service...")
    
    try:
        # Close Redis connection
        await redis_client.close()
        logger.info("Redis connection closed")
        
        logger.info("Rate Limiter service shutdown completed")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


# Create FastAPI application
app = FastAPI(
    title="Distributed Rate Limiter",
    description="""
    ## Dynamic, Load-Aware Rate Limiter for FastAPI

    This service provides comprehensive rate limiting functionality with:

    * **Tier-based limiting**: Free, Pro, and Enterprise tiers with different limits
    * **Dynamic health awareness**: Adapts limits based on system health (NORMAL/DEGRADED)
    * **Distributed consistency**: Uses Redis for shared state across multiple instances
    * **Security features**: Rate limiting for invalid API key attempts
    * **High availability**: Circuit breaker pattern and graceful degradation

    ### Authentication
    **Test endpoints only:** Include your API key in the `X-API-Key` header with requests to `/test/*` endpoints.
    Admin and info endpoints do not require authentication.

    ### Rate Limiting Behavior

    #### NORMAL System State (Maximize Utilization)
    - **Free Tier**: 10 RPM base → 20 RPM burst allowed
    - **Pro Tier**: 100 RPM base → 150 RPM burst allowed  
    - **Enterprise Tier**: 1000 RPM maintained

    #### DEGRADED System State (Load Shedding & SLA Protection)
    - **Free Tier**: Heavily throttled to 2 RPM
    - **Pro Tier**: Limited to base 100 RPM (SLA protected)
    - **Enterprise Tier**: Full 1000 RPM maintained (SLA protected)

    ### Response Headers
    All successful responses include rate limiting headers:
    - `X-RateLimit-Limit`: Current rate limit
    - `X-RateLimit-Remaining`: Remaining requests in window
    - `X-RateLimit-Reset`: Unix timestamp when limit resets

    ### Demo API Keys
    For testing purposes, use these demo API keys:
    - `demo_free_key_123` (Free tier)
    - `demo_pro_key_789` (Pro tier)  
    - `demo_enterprise_key_abc` (Enterprise tier)
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "test",
            "description": "Test endpoints for rate limiting functionality. **Requires API key authentication.**"
        },
        {
            "name": "admin",
            "description": "Administrative endpoints for system management (no authentication required for demo)."
        },
        {
            "name": "info",
            "description": "Information endpoints (no authentication required)."
        }
    ]
)

# Add CORS middleware for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add rate limiting middleware
# Exclude health check, admin, and documentation endpoints
excluded_paths = [
    "/health",
    "/",
    "/docs", 
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/admin/*"  # Exclude all admin endpoints from rate limiting and API key requirements
]

app.add_middleware(RateLimitMiddleware, exclude_paths=excluded_paths)

# Include routers
app.include_router(admin_router)
app.include_router(test_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler for unhandled errors.
    """
    logger.error(
        f"Unhandled exception in {request.method} {request.url.path}: {exc}",
        exc_info=True
    )
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "message": "An unexpected error occurred",
            "request_id": getattr(request.state, "request_id", "unknown")
        }
    )


@app.get("/", tags=["info"])
async def root():
    """
    Root endpoint with API information.
    """
    return {
        "name": "Distributed Rate Limiter API",
        "version": "1.0.0",
        "status": "operational",
        "features": [
            "Dynamic, load-aware rate limiting",
            "Multi-tier support (Free, Pro, Enterprise)",
            "Redis-based distributed storage",
            "Security rate limiting",
            "High availability design"
        ],
        "quick_start": {
            "1": "Include X-API-Key header with your requests",
            "2": "Use /test endpoint to verify rate limiting",
            "3": "Check /admin/health to see system health",
            "4": "Use /admin endpoints to manage system state",
            "5": "View /docs for complete API documentation"
        },
        "demo_keys": {
            "free": "demo_free_key_123",
            "pro": "demo_pro_key_789", 
            "enterprise": "demo_enterprise_key_abc"
        }
    }


if __name__ == "__main__":
    import uvicorn
    
    # Run the application
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
