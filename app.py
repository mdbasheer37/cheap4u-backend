# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from conpig import Config   # <-- matches the actual filename on your server
from models import db
import os
import importlib
import requests as http_requests


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    JWTManager(app)

    from auth import auth_bp
    from payment import payment_bp
    from referral import referral_bp
    from admin import admin_bp
    from plans import plans_bp

    # Support vtpass.py or routes.py
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

    # ── DEBUG ROUTES (delete after SMS confirmed working) ────────────
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
            'TERMII_API_KEY': (api_key[:6] + '...' + api_key[-4:]) if len(api_key) > 10 else ('NOT_SET' if not api_key else 'TOO_SHORT'),
            'TERMII_SENDER_ID': app.config.get('TERMII_SENDER_ID'),
            'DB_URL_PREVIEW': db_url[:40] + '...' if db_url else 'NOT_SET',
            'key_length': len(api_key),
        })

    @app.route('/api/debug/test-sms', methods=['GET', 'POST'])
    def debug_test_sms():
        if request.method == 'POST':
            phone_raw = (request.get_json() or {}).get('phone', '09037663816')
        else:
            phone_raw = request.args.get('phone', '09037663816')

        api_key = app.config.get('TERMII_API_KEY', '').strip()
        sender_id = app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()

        if not api_key:
            return jsonify({'error': 'TERMII_API_KEY not set in Render environment'}), 500

        phone_intl = _to_intl(phone_raw)
        if not phone_intl:
            return jsonify({'error': f'Cannot convert phone {phone_raw}'}), 400

        results = []
        for sender, channel in [(None, "number"), ("talert", "generic"), (sender_id, "generic")]:
            try:
                r = http_requests.post(
                    'https://api.ng.termii.com/api/sms/send',
                    json={k: v for k, v in {'api_key': api_key, 'to': phone_intl,
                          'from': sender, 'sms': 'Cheap4u test OTP: 123456. Ignore.',
                          'type': 'plain', 'channel': channel}.items() if v is not None},
                    headers={'Content-Type': 'application/json'}, timeout=15)
                try:
                    body = r.json()
                except Exception:
                    body = r.text
                success = (r.status_code == 200 and isinstance(body, dict)
                           and body.get('message') == 'Successfully Sent')
                results.append({'sender': sender, 'channel': channel,
                                 'http_status': r.status_code,
                                 'termii_response': body, 'success': success})
                if success:
                    return jsonify({'result': 'SMS_SENT', 'via': f'{sender}/{channel}',
                                    'all_attempts': results})
            except Exception as e:
                results.append({'sender': sender, 'channel': channel,
                                 'error': str(e), 'success': False})

        return jsonify({'result': 'ALL_FAILED', 'all_attempts': results}), 500
    # ── END DEBUG ROUTES ─────────────────────────────────────────────

    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({'status': 'healthy', 'message': 'Backend is running', 'version': '1.0.0'})

    @app.route('/', methods=['GET'])
    def index():
        return jsonify({'message': 'Cheap4U API is running'})

    # Create DB tables on first request (avoids Render startup DNS issues)
    @app.before_request
    def create_tables():
        if not getattr(app, '_tables_created', False):
            try:
                db.create_all()
                from init_plans import init_all
                init_all()
                app._tables_created = True
                print("✅ DB ready")
            except Exception as e:
                print(f"⚠️ DB init error: {e}")

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
