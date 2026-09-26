#!/usr/bin/env bash
# ==============================================================================
# AstraQuant - Quick Start Script
# ==============================================================================

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "🚀 [AstraQuant] Initializing system environment..."

# 1. Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed."
    exit 1
fi

# 2. Check or create .env
if [ ! -f .env ]; then
    if [ -f env.example ]; then
        echo "📝 Creating .env from env.example..."
        cp env.example .env
    else
        echo "⚠️ Warning: env.example not found, please configure .env manually."
    fi
fi

# 3. Create required runtime directories
mkdir -p data logs backups

# 3.1 Initialize default instrument pool if not present (prevents untrusted pool blocking entry)
if [ ! -f "data/instrument_pool.json" ]; then
    echo "📋 Initializing default instrument pool..."
    if [ -x ".venv/bin/python" ]; then
        .venv/bin/python -c "from scripts.instrument_pool import save_instruments, DEFAULT_INSTRUMENTS; save_instruments(DEFAULT_INSTRUMENTS)" 2>/dev/null || true
    fi
fi

# 4. Check Node.js and build frontend if dist doesn't exist
if [ ! -d "frontend/dist" ]; then
    echo "📦 Frontend production bundle not detected. Building Vue 3 SPA..."
    if command -v npm &> /dev/null; then
        cd frontend
        npm install
        npm run build
        cd "$ROOT_DIR"
    else
        echo "⚠️ Warning: npm is not installed. Please build frontend manually via 'cd frontend && npm install && npm run build'."
    fi
fi

# 5. Start Backend Engine
echo "✨ Launching AstraQuant on http://0.0.0.0:8080 ..."
exec python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
