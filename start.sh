#!/bin/bash

echo "🎥 Starting YouTube Downloader..."
echo "🔧 Environment: GitHub Codespaces"
echo "🌐 Port: 5000"
echo ""

# Check if requirements are installed
if [ ! -f ".requirements_installed" ]; then
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt
    touch .requirements_installed
    echo "✅ Dependencies installed!"
fi

# Start the Flask app
echo "🚀 Launching YouTube Downloader..."
echo "📱 Access your app at: https://${CODESPACE_NAME}-5000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
echo ""
python app.py