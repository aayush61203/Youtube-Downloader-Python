import sys
import os

# Add the parent directory to Python path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Vercel serverless handler
def handler(event, context):
    return app

# For direct import
application = app