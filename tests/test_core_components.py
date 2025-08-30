"""
Unit tests for core rate limiter components.

This module provides basic unit tests to demonstrate testing methodology
and validate core functionality of the rate limiting system.
"""

import pytest
import time
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.core.models import (
    TierConfig, RateLimitConfig, RateLimitResult, 
    APIKeyError, SystemHealth
)
from src.core.config import ConfigManager
from src.services.user_service import UserTierManager, APIKeyValidator
from src.services.rate_limit_service import RateLimitService


class TestTierConfig:
    """Test TierConfig model validation."""
    
    def test_valid_tier_config(self):
        """Test valid tier configuration."""
        config = TierConfig(
            base_limit=10,
            burst_limit=20,
            degraded_limit=5,
            window_minutes=1
        )
        assert config.base_limit == 10
        assert config.burst_limit == 20
        assert config.degraded_limit == 5
        assert config.window_minutes == 1
    
    def test_burst_limit_validation(self):
        """Test that burst limit must be >= base limit."""
        with pytest.raises(ValueError, match="burst_limit must be >= base_limit"):
            TierConfig(
                base_limit=20,
                burst_limit=10,  # Invalid: less than base
                degraded_limit=5,
                window_minutes=1
            )
    
    def test_positive_limits(self):
        """Test that all limits must be positive."""
        with pytest.raises(ValueError):
            TierConfig(
                base_limit=0,  # Invalid: must be > 0
                burst_limit=10,
                degraded_limit=5,
                window_minutes=1
            )


class TestRateLimitConfig:
    """Test RateLimitConfig model validation."""
    
    def test_valid_rate_limit_config(self):
        """Test valid rate limit configuration."""
        config = RateLimitConfig(
            tiers={
                "free": TierConfig(base_limit=10, burst_limit=20, degraded_limit=2, window_minutes=1),
                "pro": TierConfig(base_limit=100, burst_limit=150, degraded_limit=100, window_minutes=1),
                "enterprise": TierConfig(base_limit=1000, burst_limit=1000, degraded_limit=1000, window_minutes=1)
            },
            users={"user1": "free", "user2": "pro"},
            api_keys={"key1": "user1", "key2": "user2"}
        )
        assert "free" in config.tiers
        assert "pro" in config.tiers
        assert "enterprise" in config.tiers
        assert config.users["user1"] == "free"
        assert config.api_keys["key1"] == "user1"
    
    def test_missing_required_tiers(self):
        """Test validation fails when required tiers are missing."""
        with pytest.raises(ValueError, match="Missing required tiers"):
            RateLimitConfig(
                tiers={"free": TierConfig(base_limit=10, burst_limit=20, degraded_limit=2, window_minutes=1)},
                users={},
                api_keys={}
            )
    
    def test_invalid_user_tier_reference(self):
        """Test validation fails when user references invalid tier."""
        with pytest.raises(ValueError, match="references invalid tier"):
            RateLimitConfig(
                tiers={
                    "free": TierConfig(base_limit=10, burst_limit=20, degraded_limit=2, window_minutes=1),
                    "pro": TierConfig(base_limit=100, burst_limit=150, degraded_limit=100, window_minutes=1),
                    "enterprise": TierConfig(base_limit=1000, burst_limit=1000, degraded_limit=1000, window_minutes=1)
                },
                users={"user1": "invalid_tier"},
                api_keys={}
            )
    
    def test_invalid_api_key_user_reference(self):
        """Test validation fails when API key references invalid user."""
        with pytest.raises(ValueError, match="references invalid user"):
            RateLimitConfig(
                tiers={
                    "free": TierConfig(base_limit=10, burst_limit=20, degraded_limit=2, window_minutes=1),
                    "pro": TierConfig(base_limit=100, burst_limit=150, degraded_limit=100, window_minutes=1),
                    "enterprise": TierConfig(base_limit=1000, burst_limit=1000, degraded_limit=1000, window_minutes=1)
                },
                users={"user1": "free"},
                api_keys={"key1": "invalid_user"}
            )


class TestUserTierManager:
    """Test UserTierManager functionality."""
    
    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager with test data."""
        with patch('src.services.user_service.config_manager') as mock:
            mock.config = Mock()
            mock.config.api_keys = {
                "test_key_1": "user1",
                "test_key_2": "user1",
                "test_key_3": "user2"
            }
            mock.config.users = {
                "user1": "free",
                "user2": "pro"
            }
            yield mock
    
    def test_get_user_from_api_key(self, mock_config_manager):
        """Test API key to user ID resolution."""
        manager = UserTierManager()
        
        assert manager.get_user_from_api_key("test_key_1") == "user1"
        assert manager.get_user_from_api_key("test_key_2") == "user1"
        assert manager.get_user_from_api_key("test_key_3") == "user2"
        assert manager.get_user_from_api_key("invalid_key") is None
    
    def test_get_tier_from_user(self, mock_config_manager):
        """Test user ID to tier resolution."""
        manager = UserTierManager()
        
        assert manager.get_tier_from_user("user1") == "free"
        assert manager.get_tier_from_user("user2") == "pro"
        assert manager.get_tier_from_user("invalid_user") is None
    
    def test_get_user_tier(self, mock_config_manager):
        """Test API key to (user_id, tier) resolution."""
        manager = UserTierManager()
        
        result = manager.get_user_tier("test_key_1")
        assert result == ("user1", "free")
        
        result = manager.get_user_tier("test_key_3")
        assert result == ("user2", "pro")
        
        result = manager.get_user_tier("invalid_key")
        assert result is None


class TestAPIKeyValidator:
    """Test API key validation logic."""
    
    @pytest.fixture
    def mock_user_manager(self):
        """Mock user tier manager."""
        manager = Mock()
        manager.get_user_tier.return_value = ("user1", "free")
        return manager
    
    @pytest.fixture
    def validator(self, mock_user_manager):
        """Create API key validator with mocked dependencies."""
        return APIKeyValidator(mock_user_manager)
    
    def test_valid_api_key(self, validator, mock_user_manager):
        """Test validation of valid API key."""
        request_context = {"ip_address": "127.0.0.1", "user_agent": "test"}
        
        user_id, tier = validator.validate_api_key("valid_key", request_context)
        assert user_id == "user1"
        assert tier == "free"
    
    def test_missing_api_key(self, validator):
        """Test validation of missing API key."""
        request_context = {"ip_address": "127.0.0.1", "user_agent": "test"}
        
        with pytest.raises(APIKeyError) as exc_info:
            validator.validate_api_key(None, request_context)
        
        assert exc_info.value.error_code == "MISSING_API_KEY"
        assert exc_info.value.status_code == 401
    
    def test_empty_api_key(self, validator):
        """Test validation of empty API key."""
        request_context = {"ip_address": "127.0.0.1", "user_agent": "test"}
        
        with pytest.raises(APIKeyError) as exc_info:
            validator.validate_api_key("", request_context)
        
        assert exc_info.value.error_code == "EMPTY_API_KEY"
        assert exc_info.value.status_code == 401
    
    def test_malformed_api_key(self, validator):
        """Test validation of malformed API key."""
        request_context = {"ip_address": "127.0.0.1", "user_agent": "test"}
        
        with pytest.raises(APIKeyError) as exc_info:
            validator.validate_api_key("mal@formed#key!", request_context)
        
        assert exc_info.value.error_code == "MALFORMED_API_KEY"
        assert exc_info.value.status_code == 400
    
    def test_invalid_api_key(self, validator, mock_user_manager):
        """Test validation of invalid API key."""
        mock_user_manager.get_user_tier.return_value = None
        request_context = {"ip_address": "127.0.0.1", "user_agent": "test"}
        
        with pytest.raises(APIKeyError) as exc_info:
            validator.validate_api_key("invalid_key", request_context)
        
        assert exc_info.value.error_code == "INVALID_API_KEY"
        assert exc_info.value.status_code == 401


class TestRateLimitService:
    """Test rate limit service logic."""
    
    @pytest.fixture
    def mock_config_manager(self):
        """Mock configuration manager."""
        with patch('src.services.rate_limit_service.config_manager') as mock:
            mock.get_tier_config.return_value = TierConfig(
                base_limit=10,
                burst_limit=20,
                degraded_limit=2,
                window_minutes=1
            )
            yield mock
    
    @pytest.fixture
    def mock_redis_client(self):
        """Mock Redis client."""
        with patch('src.services.rate_limit_service.redis_client') as mock:
            mock.check_rate_limit = AsyncMock(return_value=(True, 5, int(time.time()) + 60))
            yield mock
    
    @pytest.fixture
    def service(self, mock_config_manager, mock_redis_client):
        """Create rate limit service with mocked dependencies."""
        return RateLimitService()
    
    def test_calculate_effective_limit_normal(self, service):
        """Test effective limit calculation in NORMAL state."""
        tier_config = TierConfig(
            base_limit=10,
            burst_limit=20,
            degraded_limit=2,
            window_minutes=1
        )
        
        # Free tier in NORMAL state should get burst limit
        limit = service._calculate_effective_limit("free", tier_config, SystemHealth.NORMAL)
        assert limit == 20
        
        # Pro tier in NORMAL state should get burst limit
        limit = service._calculate_effective_limit("pro", tier_config, SystemHealth.NORMAL)
        assert limit == 20
    
    def test_calculate_effective_limit_degraded(self, service):
        """Test effective limit calculation in DEGRADED state."""
        tier_config = TierConfig(
            base_limit=10,
            burst_limit=20,
            degraded_limit=2,
            window_minutes=1
        )
        
        # Free tier in DEGRADED state should get degraded limit
        limit = service._calculate_effective_limit("free", tier_config, SystemHealth.DEGRADED)
        assert limit == 2
        
        # Pro tier in DEGRADED state should get base limit
        limit = service._calculate_effective_limit("pro", tier_config, SystemHealth.DEGRADED)
        assert limit == 10
        
        # Enterprise tier in DEGRADED state should get base limit
        limit = service._calculate_effective_limit("enterprise", tier_config, SystemHealth.DEGRADED)
        assert limit == 10
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_allowed(self, service, mock_redis_client):
        """Test rate limit check when request is allowed."""
        with patch.object(service, '_get_system_health_cached', return_value=SystemHealth.NORMAL):
            result = await service.check_rate_limit("user1", "free")
            
            assert isinstance(result, RateLimitResult)
            assert result.allowed is True
            assert result.user_id == "user1"
            assert result.tier == "free"
            assert result.limit == 20  # Burst limit in NORMAL state
            assert result.remaining == 15  # 20 - 5 (current count from mock)
    
    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_failure(self, service, mock_redis_client):
        """Test rate limit check when Redis fails."""
        mock_redis_client.check_rate_limit.side_effect = Exception("Redis connection failed")
        
        with patch.object(service, '_get_system_health_cached', return_value=SystemHealth.NORMAL):
            result = await service.check_rate_limit("user1", "free")
            
            # Should allow request as fallback
            assert result.allowed is True
            assert result.remaining == 1  # Minimal remaining for fallback


class TestConfigManager:
    """Test configuration manager functionality."""
    
    def test_default_config_structure(self):
        """Test that default configuration has required structure."""
        with patch('src.core.config.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            manager = ConfigManager()
            config = manager.config
            
            # Check required tiers exist
            assert "free" in config.tiers
            assert "pro" in config.tiers
            assert "enterprise" in config.tiers
            
            # Check demo users exist
            assert len(config.users) > 0
            assert len(config.api_keys) > 0
            
            # Check Redis config
            assert config.redis.host == "localhost"
            assert config.redis.port == 6379


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
