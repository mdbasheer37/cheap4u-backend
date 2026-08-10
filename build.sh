#!/usr/bin/env bash
# Render build script — runs before every deploy
set -o errexit

echo "📦 Installing Python packages..."
pip install -r requirements.txt

echo "🗄️  Database will be initialised on first request (via lifespan)"
echo "✅ Build complete"
