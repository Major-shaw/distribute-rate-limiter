"""
System health management service.

This module provides services for managing system health status and performing
component health checks.
"""

import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from ..core.models import SystemHealth
from ..core.config import config_manager
from ..core.redis_client import redis_client

logger = logging.getLogger(__name__)


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
