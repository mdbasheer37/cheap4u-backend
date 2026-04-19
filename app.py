# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from config import Config
from models import db
import os
import importlib
import requests as http_requests


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app, resources={r"/api/*": {"origins": os.getenv("ALLOWED_ORIGINS", "*")}})
    db.init_app(app)
    JWTManager(app)

    from auth import auth_bp
    from payment import payment_bp
    from referral import referral_bp
    from admin import admin_bp
    from plans import plans_bp

    # Support both vtpass.py and routes.py filenames
    vtpass_bp = None
    for mod_name in ('vtpass', 'routes'):
        try:
            mod = importlib.import_module(mod_name)
            for attr in ('vtpass_bp', 'bp', 'main'):
                if hasattr(mod, attr):
                    vtpass_bp = getattr(mod, attr)
                    break
            if vtpass_bp:
                break
        except ImportError:
            continue

    app.register_blueprint(auth_bp)
    app.register_blueprint(payment_bp)
    if vtpass_bp:
        app.register_blueprint(vtpass_bp)
    app.register_blueprint(referral_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)

    # ── TEMPORARY DEBUG ROUTES ────────────────────────────────────────
    def _to_intl(phone):
        phone = str(phone).strip().replace(" ", "").replace("-", "")
        if phone.startswith("0") and len(phone) == 11 and phone.isdigit():
            return "234" + phone[1:]
        elif phone.startswith("234") and len(phone) == 13 and phone.isdigit():
            return phone
        return None

    @app.route('/api/debug/check-config', methods=['GET'])
    def debug_check_config():
        api_key = app.config.get('TERMII_API_KEY', '').strip()
        db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        return jsonify({
            'TERMII_API_KEY': (api_key[:6] + '...' + api_key[-4:]) if len(api_key) > 10 else ('NOT_SET' if not api_key else 'SET_SHORT'),
            'TERMII_SENDER_ID': app.config.get('TERMII_SENDER_ID', 'NOT SET'),
            'DB_URL_PREVIEW': db_url[:30] + '...' if db_url else 'NOT SET',
            'key_length': len(api_key),
        })

    @app.route('/api/debug/test-sms', methods=['POST'])
    def debug_test_sms():
        data = request.get_json() or {}
        phone_raw = data.get('phone', '09037663816')
        api_key = app.config.get('TERMII_API_KEY', '').strip()
        sender_id = app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()
        if not api_key:
            return jsonify({'error': 'TERMII_API_KEY not set'}), 500
        phone_intl = _to_intl(phone_raw)
        if not phone_intl:
            return jsonify({'error': f'Bad phone: {phone_raw}'}), 400
        results = []
        for sender, channel in [(sender_id, 'dnd'), ('N-Alert', 'dnd'), ('N-Alert', 'generic')]:
            try:
                r = http_requests.post(
                    'https://api.ng.termii.com/api/sms/send',
                    json={'api_key': api_key, 'to': phone_intl, 'from': sender,
                          'sms': 'Cheap4u test: 123456', 'type': 'plain', 'channel': channel},
                    headers={'Content-Type': 'application/json'}, timeout=15)
                try:
                    body = r.json()
                except Exception:
                    body = r.text
                success = r.status_code == 200 and isinstance(body, dict) and body.get('message') == 'Successfully Sent'
                results.append({'sender': sender, 'channel': channel, 'http_status': r.status_code, 'response': body, 'success': success})
                if success:
                    return jsonify({'result': 'SMS_SENT', 'via': f'{sender}/{channel}', 'attempts': results})
            except Exception as e:
                results.append({'sender': sender, 'channel': channel, 'error': str(e), 'success': False})
        return jsonify({'result': 'ALL_FAILED', 'attempts': results}), 500
    # ── END DEBUG ROUTES ─────────────────────────────────────────────

    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'healthy', 'message': 'Backend is running', 'version': '1.0.0'})

    @app.route('/', methods=['GET'])
    def index():
        return jsonify({'message': 'Cheap4U API is running'})

    # FIXED: Use before_request to run db.create_all() lazily on first request
    # instead of at startup — avoids the DNS/network not ready error on Render
    @app.before_request
    def create_tables():
        # Only run once
        if not getattr(app, '_tables_created', False):
            try:
                db.create_all()
                from init_plans import init_all
                init_all()
                app._tables_created = True
                print("✅ Database tables created/verified")
            except Exception as e:
                print(f"⚠️  DB init error (will retry): {e}")

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
