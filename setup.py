#!/usr/bin/env python3
"""
🚀 YouTube Downloader - Quick Setup Script
Run this script to quickly set up and start the application
"""

import subprocess
import sys
import os

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"🔄 {description}...")
    try:
        subprocess.run(command, shell=True, check=True)
        print(f"✅ {description} completed!")
        return True
    except subprocess.CalledProcessError:
        print(f"❌ {description} failed!")
        return False

def main():
    print("🎬 YouTube Downloader - Setup Script")
    print("=" * 50)
    
    # Check Python version
    if sys.version_info < (3, 11):
        print("❌ Python 3.11 or higher is required!")
        print(f"   Current version: {sys.version}")
        return False
    
    print(f"✅ Python version: {sys.version.split()[0]}")
    
    # Install dependencies
    if not run_command("pip install -r requirements.txt", "Installing dependencies"):
        return False
    
    # Create downloads directory
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
        print("✅ Created downloads directory")
    
    print("\n🎉 Setup completed successfully!")
    print("\n🚀 To start the application:")
    print("   python app.py")
    print("\n🌐 Then open: http://localhost:5000")
    print("\n📖 For deployment help, see: DEPLOY.md")
    
    return True

if __name__ == "__main__":
    success = main()
    if not success:
        sys.exit(1)