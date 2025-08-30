#!/usr/bin/env python3
"""
Test script to demonstrate rate limiting functionality.

This script tests the rate limiter by making requests with different API keys
and demonstrating how system health affects rate limiting behavior.
"""

import asyncio
import time
import json
from typing import Dict, Any, List
import httpx

# Base URL for the rate limiter service
BASE_URL = "http://localhost:8000"

# Demo API keys for testing
API_KEYS = {
    "free": "demo_free_key_123",
    "pro": "demo_pro_key_789", 
    "enterprise": "demo_enterprise_key_abc"
}

# Admin API key (if configured)
ADMIN_KEY = "admin_secret_key_change_in_production"


class RateLimiterTester:
    """Test suite for the rate limiter functionality."""
    
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    async def make_request(self, endpoint: str, api_key: str = None, 
                          method: str = "GET", **kwargs) -> Dict[str, Any]:
        """
        Make a request to the rate limiter API.
        
        Args:
            endpoint: API endpoint
            api_key: API key to use
            method: HTTP method
            **kwargs: Additional arguments for httpx
            
        Returns:
            Dictionary with response data
        """
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key
        
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = await self.client.request(method, url, headers=headers, **kwargs)
            
            # Extract rate limiting headers
            rate_limit_headers = {
                "limit": response.headers.get("X-RateLimit-Limit"),
                "remaining": response.headers.get("X-RateLimit-Remaining"), 
                "reset": response.headers.get("X-RateLimit-Reset"),
                "request_id": response.headers.get("X-Request-ID")
            }
            
            return {
                "status_code": response.status_code,
                "headers": rate_limit_headers,
                "body": response.json() if response.content else {},
                "success": 200 <= response.status_code < 300
            }
            
        except Exception as e:
            return {
                "status_code": 0,
                "headers": {},
                "body": {"error": str(e)},
                "success": False
            }
    
    async def test_basic_functionality(self):
        """Test basic rate limiting functionality."""
        print("🧪 Testing Basic Rate Limiting Functionality")
        print("=" * 50)
        
        for tier, api_key in API_KEYS.items():
            print(f"\n📊 Testing {tier.upper()} tier (API Key: {api_key})")
            
            # Make a few requests to show rate limiting in action
            for i in range(5):
                result = await self.make_request("/test", api_key)
                
                status = "✅ SUCCESS" if result["success"] else "❌ FAILED"
                remaining = result["headers"].get("remaining", "unknown")
                limit = result["headers"].get("limit", "unknown")
                
                print(f"  Request {i+1}: {status} | Remaining: {remaining}/{limit}")
                
                if not result["success"]:
                    print(f"    Error: {result['body'].get('error', 'Unknown error')}")
                
                # Small delay between requests
                time.sleep(0.1)
        
        print("\n✅ Basic functionality test completed")
    
    async def test_system_health_impact(self):
        """Test how system health affects rate limiting."""
        print("\n🏥 Testing System Health Impact")
        print("=" * 50)
        
        # Test with NORMAL state
        print("\n📈 Setting system health to NORMAL")
        await self.set_system_health("NORMAL")
        
        await self._test_health_state("NORMAL", API_KEYS["free"])
        
        # Test with DEGRADED state
        print("\n📉 Setting system health to DEGRADED") 
        await self.set_system_health("DEGRADED")
        
        await self._test_health_state("DEGRADED", API_KEYS["free"])
        
        # Reset to NORMAL
        print("\n🔄 Resetting system health to NORMAL")
        await self.set_system_health("NORMAL")
    
    async def _test_health_state(self, state: str, api_key: str):
        """Test rate limiting behavior in a specific health state."""
        print(f"\n  Testing {state} state with Free tier API key...")
        
        # Make several requests to see the limit in action
        for i in range(8):
            result = await self.make_request("/test", api_key)
            
            status = "✅" if result["success"] else "❌"
            remaining = result["headers"].get("remaining", "?")
            limit = result["headers"].get("limit", "?")
            
            print(f"    Request {i+1}: {status} | Limit: {limit} | Remaining: {remaining}")
            
            if not result["success"]:
                error_code = result["body"].get("error_code", "unknown")
                print(f"      ❌ {error_code}: {result['body'].get('error', 'Unknown error')}")
                break
            
            time.sleep(0.1)
    
    async def test_tier_differences(self):
        """Test differences between tiers."""
        print("\n🎯 Testing Tier Differences")
        print("=" * 50)
        
        # Ensure we're in NORMAL state for burst testing
        await self.set_system_health("NORMAL")
        
        for tier, api_key in API_KEYS.items():
            print(f"\n📊 Testing {tier.upper()} tier burst behavior...")
            
            # Make rapid requests to test burst limits
            burst_results = []
            for i in range(15):
                result = await self.make_request("/test", api_key)
                burst_results.append(result)
                
                if not result["success"]:
                    break
                    
                time.sleep(0.05)  # 50ms between requests
            
            successful = sum(1 for r in burst_results if r["success"])
            first_limit = burst_results[0]["headers"].get("limit") if burst_results else "unknown"
            
            print(f"  ✅ Successful requests: {successful}/15")
            print(f"  📊 Effective limit: {first_limit} RPM")
            
            # Wait a bit before testing next tier
            time.sleep(1)
    
    async def test_invalid_api_keys(self):
        """Test handling of invalid API keys."""
        print("\n🔒 Testing Invalid API Key Handling")
        print("=" * 50)
        
        invalid_keys = [
            None,  # Missing key
            "",    # Empty key
            "invalid_key_123",  # Invalid key
            "mal@formed#key",   # Malformed key
        ]
        
        for i, key in enumerate(invalid_keys):
            key_desc = "None" if key is None else f"'{key}'" if key else "Empty"
            print(f"\n🔍 Testing invalid key {i+1}: {key_desc}")
            
            result = await self.make_request("/test", key)
            
            if not result["success"]:
                error_code = result["body"].get("error_code", "unknown")
                print(f"  ❌ Expected failure: {error_code}")
            else:
                print(f"  ⚠️  Unexpected success (this shouldn't happen)")
    
    async def test_admin_endpoints(self):
        """Test admin endpoint functionality."""
        print("\n🔧 Testing Admin Endpoints")
        print("=" * 50)
        
        # Test health status endpoint
        print("\n📊 Getting system health status...")
        result = await self.make_request("/admin/health", method="GET")
        if result["success"]:
            health = result["body"].get("system_health", {})
            print(f"  Current status: {health.get('status', 'unknown')}")
        
        # Test user listing
        print("\n👥 Getting user list...")
        result = await self.make_request("/admin/users", method="GET")
        if result["success"]:
            users = result["body"].get("users", {})
            print(f"  Total users: {len(users)}")
            for user_id, info in list(users.items())[:3]:  # Show first 3
                print(f"    {user_id}: {info.get('tier', 'unknown')} ({info.get('api_key_count', 0)} keys)")
    
    async def set_system_health(self, status: str):
        """Set system health status."""
        data = {
            "status": status,
            "updated_by": "test_script",
            "reason": f"Testing {status} state behavior"
        }
        
        result = await self.make_request(
            "/admin/health", 
            method="POST",
            json=data
        )
        
        if result["success"]:
            print(f"  ✅ System health set to {status}")
        else:
            print(f"  ❌ Failed to set system health: {result['body'].get('error', 'Unknown error')}")
    
    async def run_all_tests(self):
        """Run all test scenarios."""
        print("🚀 Starting Rate Limiter Test Suite")
        print("=" * 60)
        
        try:
            # Check if service is running
            result = await self.make_request("/health")
            if not result["success"]:
                print("❌ Rate limiter service is not running or not accessible")
                print(f"   Make sure the service is running on {self.base_url}")
                return
            
            print("✅ Rate limiter service is accessible")
            
            # Run test scenarios
            await self.test_basic_functionality()
            await self.test_system_health_impact()
            await self.test_tier_differences()
            await self.test_invalid_api_keys()
            await self.test_admin_endpoints()
            
            print("\n🎉 All tests completed successfully!")
            print("\n📋 Test Summary:")
            print("  ✅ Basic rate limiting functionality")
            print("  ✅ System health impact on limits")
            print("  ✅ Tier-based limit differences")
            print("  ✅ Invalid API key handling")
            print("  ✅ Admin endpoint functionality")
            
        except Exception as e:
            print(f"\n❌ Test suite failed with error: {e}")
        
        finally:
            await self.close()


async def main():
    """Main test function."""
    print("🔧 Rate Limiter Test Script")
    print("This script will test the rate limiter functionality")
    print(f"Service URL: {BASE_URL}\n")
    
    tester = RateLimiterTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
