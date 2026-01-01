"""
Akson Configuration
Loads environment variables and provides configuration constants
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Keys
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Fallback: try to read from bundled API key file
if not OPENAI_API_KEY:
    try:
        # Look for api_key.txt in the same directory as this config file
        key_file = os.path.join(os.path.dirname(__file__), 'api_key.txt')
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                OPENAI_API_KEY = f.read().strip()
    except:
        pass
FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "AIzaSyAwg89xejL4Oh4hGjpSfluOxZyqCqT5iaA")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "farooqi-ai")

# Service URLs
AKSON_PAYMENTS_URL = os.getenv("AKSON_PAYMENTS_URL", "https://farooqi-payments.onrender.com")

# Stripe Configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")

def require_env(var: str) -> str:
    """
    Require an environment variable to be set
    
    Args:
        var: Environment variable name
        
    Returns:
        The environment variable value
        
    Raises:
        RuntimeError: If the environment variable is not set
    """
    val = os.getenv(var)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return val

def validate_config():
    """
    Validate that all required configuration is present
    
    Raises:
        RuntimeError: If required configuration is missing
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set. Please set it as an environment variable or in a .env file.")
    
    # Firebase keys have defaults, so only warn if truly missing
    # (they're optional for basic functionality)
