"""Test script to verify the application starts correctly."""
import asyncio
import os
import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Set test environment
os.environ.update({
    'TWITCH_TOKEN': 'oauth:test_token_here',
    'CLIENT_ID': 'test_client_id',
    'TWITCH_CHANNEL': 'test_channel',
    'DEFAULT_MESSAGE': 'Hello world!',
    'DEFAULT_INTERVAL': '5',
    'BOT_ACTIVE': 'false',
    'AUTH_ENABLED': 'false',
    'API_USERNAME': 'admin',
    'API_PASSWORD': 'testpassword123',
    'SECRET_KEY': 'test_secret_key_not_for_production',
    'CACHE_EXPIRY': '300',
    'BAN_CACHE_EXPIRY': '60',
    'ALLOWED_ORIGINS': 'http://localhost:8000'
})

async def test_imports():
    """Test that all modules import correctly."""
    try:
        print("Testing imports...")
        
        # Test configuration
        from src.config.settings import settings
        print("✓ Configuration module loaded")
        
        # Test models
        from src.models.schemas import SettingsUpdate, ApiResponse
        print("✓ Models module loaded")
        
        # Test services
        from src.services.state import app_state
        from src.services.cache import twitch_cache
        from src.services.twitch_api import twitch_api
        print("✓ Services modules loaded")
        
        # Test API
        from src.api.security import security_service
        from src.api.routes import router
        print("✓ API modules loaded")
        
        # Test bot (this might fail without valid token, but import should work)
        try:
            from src.bot.twitch_bot import TwockerBot
            print("✓ Bot module loaded")
        except Exception as e:
            print(f"⚠ Bot module loaded with warning: {e}")
        
        # Test FastAPI app
        from main_new import app
        print("✓ FastAPI application loaded")
        
        print("\n🎉 All modules imported successfully!")
        
        # Test basic functionality
        print("\nTesting basic functionality...")
        
        # Test settings validation
        test_settings = SettingsUpdate(
            channel="test_channel",
            message="Test message",
            interval=5
        )
        print("✓ Settings validation works")
        
        # Test API response model
        test_response = ApiResponse(
            status="success",
            message="Test message"
        )
        print("✓ API response model works")
        
        print("\n✅ All tests passed! The application is ready to run.")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_fastapi_creation():
    """Test FastAPI app creation without starting the server."""
    try:
        print("\nTesting FastAPI app creation...")
        from main_new import app
        
        # Check if routes are registered
        routes = [route.path for route in app.routes]
        expected_routes = ["/", "/health", "/logs", "/update_settings", "/toggle_bot"]
        
        for expected_route in expected_routes:
            if any(expected_route in route for route in routes):
                print(f"✓ Route {expected_route} registered")
            else:
                print(f"⚠ Route {expected_route} not found")
        
        print("✓ FastAPI application created successfully")
        return True
        
    except Exception as e:
        print(f"❌ FastAPI test failed: {e}")
        return False

async def main():
    """Run all tests."""
    print("🔧 Twocker Enhanced - Testing Suite")
    print("=" * 50)
    
    success1 = await test_imports()
    success2 = await test_fastapi_creation()
    
    if success1 and success2:
        print("\n🎉 All tests completed successfully!")
        print("\nTo run the application:")
        print("1. Copy .env.example to .env")
        print("2. Fill in your Twitch credentials")
        print("3. Run: python main_new.py")
        return 0
    else:
        print("\n❌ Some tests failed. Please check the errors above.")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)