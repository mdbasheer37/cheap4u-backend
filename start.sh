#!/bin/bash
# start.sh — Run this instead of gunicorn directly
# It will print the actual Python error before exiting

echo "=== Testing app import ==="
python -c "
import traceback
try:
    from app import app
    print('✅ App imported successfully')
except Exception as e:
    print('❌ Import failed:')
    traceback.print_exc()
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "=== App failed to import. Check errors above ==="
    exit 1
fi

echo "=== Starting gunicorn ==="
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120 --log-level debug
 
