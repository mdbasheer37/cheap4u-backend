# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from conpig import Config
from models import db
import os
import importlib
import requests as http_requests
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Log which database we are connecting to (masked)
    db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    logger.info(f"DB: {db_url[:40]}..." if len(db_url) > 40 else f"DB: {db_url}")

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    JWTManager(app)

    # ── Register blueprints ──────────────────────────────────────────
    from auth import auth_bp
    from payment import payment_bp
    from admin import admin_bp
    from plans import plans_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)

    try:
        from referral import referral_bp
        app.register_blueprint(referral_bp)
    except ImportError:
        logger.warning('referral.py not found — skipping')

    # vtpass / routes blueprint
    vtpass_bp = None
    for mod_name in ('vtpass', 'routes'):
        try:
            mod = importlib.import_module(mod_name)
            for attr in ('vtpass_bp', 'bp', 'main'):
                if hasattr(mod, attr):
                    from flask import Blueprint
                    candidate = getattr(mod, attr)
                    if isinstance(candidate, Blueprint):
                        vtpass_bp = candidate
                        break
            if vtpass_bp:
                break
        except ImportError:
            continue
        except Exception as e:
            logger.warning(f'Could not load {mod_name}: {e}')

    if vtpass_bp:
        app.register_blueprint(vtpass_bp)
        logger.info('vtpass blueprint loaded')
    else:
        logger.warning('vtpass blueprint not found — VTU routes unavailable')

    try:
        from routes import a2c_bp
        app.register_blueprint(a2c_bp)
        logger.info('airtime-to-cash blueprint loaded')
    except ImportError:
        logger.warning('airtime_to_cash.py not found — skipping')

    # ── Debug routes ────────────────────────────────────────────────
    def _to_intl(phone):
        phone = str(phone).strip().replace(' ', '').replace('-', '')
        if phone.startswith('0') and len(phone) == 11 and phone.isdigit():
            return '234' + phone[1:]
        elif phone.startswith('234') and len(phone) == 13 and phone.isdigit():
            return phone
        return None

    @app.route('/api/debug/add-plan-type-column', methods=['GET'])
    def add_plan_type_column():
        """
        ONE-TIME MIGRATION: adds the new plan_type column to the existing
        data_plans table on the live database. db.create_all() only creates
        tables that don't exist yet - it never ALTERs an existing table, so
        this has to be run manually once after deploying the plan_type
        model change.

        Visit this URL once (GET request) after deploying, then it's safe
        to leave in place - it's idempotent (IF NOT EXISTS) and can be
        called repeatedly without harm. You can delete this route later.
        """
        from sqlalchemy import text
        try:
            with db.engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE data_plans ADD COLUMN IF NOT EXISTS "
                    "plan_type VARCHAR(30) NOT NULL DEFAULT 'Gifting'"
                ))
                conn.commit()
            # Re-run seed/backfill so plan_type values match init_plans.py
            from init_plans import init_data_plans
            init_data_plans()
            return jsonify({
                'status': 'success',
                'message': 'plan_type column added (or already existed) and plans backfilled.'
            })
        except Exception as e:
            logger.error(f'add_plan_type_column error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/debug/fix-referral-bonus', methods=['GET'])   
    def fix_referral_bonus():
        """
        Force-pay ₦50 referral bonus to a specific referrer
        for ALL users they referred, regardless of referral_bonus_claimed flag.
        """
        from models import db, User, ReferralTransaction
        import json

        referrer_id = request.args.get('referrer_id', type=int)
        if not referrer_id:
            # Show all referrers and their referred users
            referrers = db.session.query(
                User.id, User.name, User.email,
                User.referral_balance, User.referral_earnings
            ).filter(
                User.id.in_(
                    db.session.query(User.referred_by_user_id).filter(
                        User.referred_by_user_id != None
                    )
                )
            ).all()
            return jsonify({
                'referrers': [{
                    'id': r.id, 'name': r.name, 'email': r.email,
                    'referral_balance': r.referral_balance,
                    'referral_earnings': r.referral_earnings,
                    'referred_users': [{
                        'id': u.id, 'name': u.name,
                        'wallet_balance': u.wallet_balance,
                        'bonus_claimed': u.referral_bonus_claimed,
                    } for u in User.query.filter_by(referred_by_user_id=r.id).all()]
                } for r in referrers],
                'usage': 'Add ?referrer_id=X to pay bonus to that referrer'
            })

        referrer = User.query.get(referrer_id)
        if not referrer:
            return jsonify({'error': f'Referrer {referrer_id} not found'})

        referred_users = User.query.filter_by(referred_by_user_id=referrer_id).all()
        results = []
        total_paid = 0.0

        for user in referred_users:
            # Pay ₦50 for EVERY referred user (reset and repay)
            bonus = 50.0
            referrer.referral_balance  = round(referrer.referral_balance + bonus, 2)
            referrer.referral_earnings = round(referrer.referral_earnings + bonus, 2)
            user.referral_bonus_claimed = True

            # Check if ReferralTransaction already exists
            existing = ReferralTransaction.query.filter_by(
                referrer_id=referrer_id,
                referred_user_id=user.id,
                type='signup_bonus',
            ).first()

            if not existing:
                db.session.add(ReferralTransaction(
                    referrer_id      = referrer_id,
                    referred_user_id = user.id,
                    amount           = bonus,
                    type             = 'signup_bonus',
                ))

            total_paid += bonus
            results.append({
                'user_id':   user.id,
                'user_name': user.name,
                'bonus_paid': bonus,
                'had_existing_tx': existing is not None,
            })

        db.session.commit()

        return jsonify({
            'status':   'success',
            'referrer': {
                'id':               referrer.id,
                'name':             referrer.name,
                'referral_balance': referrer.referral_balance,
                'referral_earnings': referrer.referral_earnings,
            },
            'total_paid':    total_paid,
            'users_paid':    results,
            'message':       f'₦{total_paid:,.2f} added to {referrer.name} referral balance',
        })    
    @app.route('/run-migration', methods=['GET'])     
    def run_migration():
        try:
            db.session.execute(db.text(
                "ALTER TABLE withdrawal_requests "
                "ADD COLUMN IF NOT EXISTS transfer_code VARCHAR(100);"
        ))
            db.session.commit()
            return jsonify({'status': 'success', 'message': 'Column added!'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}) 
        
    @app.route('/api/debug/check-config', methods=['GET'])
    def debug_check_config():
        api_key = app.config.get('TERMII_API_KEY', '').strip()
        db_url  = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        ps_key  = app.config.get('PAYSTACK_SECRET_KEY', '').strip()
        return jsonify({
            'TERMII_API_KEY':      (api_key[:6]+'...'+api_key[-4:]) if len(api_key)>10 else 'NOT_SET',
            'TERMII_SENDER_ID':    app.config.get('TERMII_SENDER_ID'),
            'PAYSTACK_SECRET_KEY': (ps_key[:8]+'...') if ps_key else 'NOT_SET',
            'DB_URL_PREVIEW':      db_url[:50]+'...' if len(db_url)>50 else db_url,
            'DB_TYPE':             'postgresql' if 'postgresql' in db_url else 'sqlite',
        })

    @app.route('/api/debug/test-sms', methods=['GET', 'POST'])
    def debug_test_sms():
        if request.method == 'POST':
            phone_raw = (request.get_json() or {}).get('phone', '09037663816')
        else:
            phone_raw = request.args.get('phone', '09037663816')
        api_key  = app.config.get('TERMII_API_KEY', '').strip()
        sender_id = app.config.get('TERMII_SENDER_ID', 'Cheap4uApp').strip()
        if not api_key:
            return jsonify({'error': 'TERMII_API_KEY not set'}), 500
        phone_intl = _to_intl(phone_raw)
        if not phone_intl:
            return jsonify({'error': f'Bad phone: {phone_raw}'}), 400
        results = []
        for sender, channel in [(None, 'number'), ('talert', 'generic'), (sender_id, 'generic')]:
            payload = {k: v for k, v in {
                'api_key': api_key, 'to': phone_intl, 'from': sender,
                'sms': 'Cheap4u test OTP: 123456. Ignore.',
                'type': 'plain', 'channel': channel,
            }.items() if v is not None}
            try:
                r = http_requests.post(
                    'https://api.ng.termii.com/api/sms/send',
                    json=payload, headers={'Content-Type': 'application/json'}, timeout=15)
                try:
                    body = r.json()
                except Exception:
                    body = r.text
                success = r.status_code == 200 and isinstance(body, dict) and body.get('message') == 'Successfully Sent'
                results.append({'sender': sender, 'channel': channel,
                                 'http_status': r.status_code, 'response': body, 'success': success})
                if success:
                    return jsonify({'result': 'SMS_SENT', 'via': f'{sender}/{channel}', 'attempts': results})
            except Exception as e:
                results.append({'sender': sender, 'channel': channel, 'error': str(e), 'success': False})
        return jsonify({'result': 'ALL_FAILED', 'attempts': results}), 500

    # ── Core routes ──────────────────────────────────────────────────
    @app.route('/health', methods=['GET'])
    def health_check():
        db_ok = False
        try:
            db.session.execute(db.text('SELECT 1'))
            db_ok = True
        except Exception:
            pass
        return jsonify({
            'status':   'healthy',
            'message':  'Cheap4U backend running',
            'database': 'connected' if db_ok else 'disconnected',
            'version':  '1.0.0',
        })

    @app.route('/', methods=['GET'])
    def index():
        return jsonify({'message': 'Cheap4U API is running'})

    # ── Create DB tables on first request ────────────────────────────
    # Using before_request avoids DNS-not-ready errors at startup
    @app.before_request
    def create_tables():
        if not getattr(app, '_tables_created', False):
            try:
                db.create_all()
                try:
                    from init_plans import init_all
                    init_all()
                except Exception as e:
                    logger.warning(f'init_plans error (non-fatal): {e}')
                app._tables_created = True
                logger.info('✅ DB tables ready')
            except Exception as e:
                logger.error(f'DB init error: {e}')

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
