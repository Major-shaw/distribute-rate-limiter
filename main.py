"""
Main FastAPI application with distributed rate limiting.

This is the entry point for the rate limiting service that demonstrates
dynamic, load-aware rate limiting with Redis-based distributed storage.
"""

import logging
import asyncio
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
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

# Configure comprehensive logging
def setup_logging():
    """Setup comprehensive logging to both console and files."""
    # Ensure logs directory exists
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Create custom formatter with extra fields
    class CustomFormatter(logging.Formatter):
        def format(self, record):
            # Add request_id to the format if available
            if hasattr(record, 'request_id'):
                record.req_id = f"[{record.request_id}] "
            else:
                record.req_id = ""
            
            # Add operation markers if available
            operations = []
            for attr in ['lifecycle_stage', 'service_operation', 'redis_operation', 
                        'user_service_operation', 'config_operation']:
                if hasattr(record, attr):
                    operations.append(f"{attr}={getattr(record, attr)}")
            
            if operations:
                record.operations = f" [{', '.join(operations)}]"
            else:
                record.operations = ""
            
            return super().format(record)
    
    # Format string with request ID and operations
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(req_id)s%(message)s%(operations)s'
    formatter = CustomFormatter(log_format)
    
    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    
    # Console handler (INFO and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Main application log file (INFO and above) with rotation
    main_file_handler = RotatingFileHandler(
        log_dir / "rate_limiter.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    main_file_handler.setLevel(logging.INFO)
    main_file_handler.setFormatter(formatter)
    root_logger.addHandler(main_file_handler)
    
    # Debug log file (DEBUG and above) with rotation
    debug_file_handler = RotatingFileHandler(
        log_dir / "rate_limiter_debug.log",
        maxBytes=50*1024*1024,  # 50MB
        backupCount=3
    )
    debug_file_handler.setLevel(logging.DEBUG)
    debug_file_handler.setFormatter(formatter)
    root_logger.addHandler(debug_file_handler)
    
    # Security events log file (WARNING and above)
    security_file_handler = RotatingFileHandler(
        log_dir / "security.log",
        maxBytes=10*1024*1024,  # 10MB
        backupCount=10
    )
    security_file_handler.setLevel(logging.WARNING)
    security_file_handler.setFormatter(formatter)
    root_logger.addHandler(security_file_handler)
    
    # Rate limiting specific log file
    rate_limit_handler = RotatingFileHandler(
        log_dir / "rate_limiting.log",
        maxBytes=20*1024*1024,  # 20MB
        backupCount=5
    )
    rate_limit_handler.setLevel(logging.INFO)
    rate_limit_handler.setFormatter(formatter)
    
    # Add filter to only log rate limiting related messages
    class RateLimitFilter(logging.Filter):
        def filter(self, record):
            # Log if it's from rate limiting components or has rate limit keywords
            rate_limit_components = [
                'src.middleware.rate_limiter',
                'src.services.rate_limit_service',
                'src.core.redis_client'
            ]
            
            if record.name in rate_limit_components:
                return True
            
            # Check for rate limiting keywords in the message
            keywords = ['rate limit', 'rate_limit', 'remaining', 'allowed', 'exceeded']
            message = record.getMessage().lower()
            return any(keyword in message for keyword in keywords)
    
    rate_limit_handler.addFilter(RateLimitFilter())
    root_logger.addHandler(rate_limit_handler)
    
    # Set root logger level
    root_logger.setLevel(logging.DEBUG)
    
    # Configure specific loggers
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)  # Reduce access log noise
    logging.getLogger('fastapi').setLevel(logging.INFO)
    
    # Rate limiter components - more verbose
    logging.getLogger('src.middleware.rate_limiter').setLevel(logging.DEBUG)
    logging.getLogger('src.services.rate_limit_service').setLevel(logging.DEBUG)
    logging.getLogger('src.services.user_service').setLevel(logging.DEBUG)
    logging.getLogger('src.core.redis_client').setLevel(logging.DEBUG)
    logging.getLogger('src.core.config').setLevel(logging.INFO)
    
    print(f"✓ Logging configured - logs will be written to: {log_dir.absolute()}")
    print("  - rate_limiter.log (INFO+)")
    print("  - rate_limiter_debug.log (DEBUG+)")
    print("  - security.log (WARNING+)")
    print("  - rate_limiting.log (rate limiting specific)")

# Setup logging
setup_logging()
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

# Add security scheme to OpenAPI
app.openapi_schema = None  # Reset to regenerate with security

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # Add security scheme only for test endpoints
    openapi_schema["components"]["securitySchemes"] = {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API key for authentication. Use demo keys: demo_free_key_123 (Free), demo_pro_key_789 (Pro), demo_enterprise_key_abc (Enterprise)"
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

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
