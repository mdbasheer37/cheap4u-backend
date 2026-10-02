# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from werkzeug.middleware.proxy_fix import ProxyFix
from conpig import Config
from models import db
import os
import importlib
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Columns the current models expect on tables that may already exist in an
# older live database. db.create_all() never adds columns to an existing table,
# and if any of these are missing every User/OTP query fails with "column ...
# does not exist" — which takes down sign-up, login AND the whole OTP flow.
# They are applied automatically at boot (see _init_db_and_extras) and are all
# idempotent; /api/debug/add-pin-columns still exists and runs the same list.
AUTH_COLUMN_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_pin_hash VARCHAR(200)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_pin_set_at TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_pin_failed_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_pin_locked_until TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS transaction_pin_set_at TIMESTAMP",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS transaction_pin_failed_attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS transaction_pin_locked_until TIMESTAMP",
    "ALTER TABLE otps ADD COLUMN IF NOT EXISTS provider_message_id VARCHAR(100)",
]


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Render runs a reverse proxy in front of gunicorn, so without this
    # request.remote_addr is the PROXY's address for every request and every
    # per-IP rate limit (login, register, ...) is shared by ALL users combined.
    # ProxyFix reads the Nth entry counting from the RIGHT of X-Forwarded-For —
    # the entries the platform's own proxies appended, which a client cannot
    # forge (anything a client sends sits further left). N defaults to 1; if
    # /api/debug/check-config (with ENABLE_DEBUG_ROUTES=true) shows that
    # client_ip_seen is NOT your own public IP, set TRUSTED_PROXY_HOPS=2 on the
    # server (one more proxy sits in front of the app). Never set it higher than
    # the number of proxies actually in front, or clients could spoof their IP.
    try:
        _hops = max(1, int(os.getenv('TRUSTED_PROXY_HOPS', '1')))
    except ValueError:
        _hops = 1
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_hops)

    # Log which database we are connecting to (masked)
    db_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    logger.info(f"DB: {db_url[:40]}..." if len(db_url) > 40 else f"DB: {db_url}")

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    db.init_app(app)
    JWTManager(app)

    # Rate limiter (shared with ai_chat.py via extensions.py to avoid
    # circular imports)
    from extensions import limiter
    limiter.init_app(app)

    # Flask-Limiter's default 429 is an HTML page, which the mobile app can't
    # parse (users just saw "Server error (HTTP 429)"). Answer in the same JSON
    # shape as every other error so the app can show a readable message.
    @app.errorhandler(429)
    def _too_many_requests(e):
        return jsonify({
            'status':  'error',
            'message': 'Too many attempts. Please wait a few minutes and try again.',
        }), 429

    # ── Register blueprints ──────────────────────────────────────────
    from auth import auth_bp
    from payment import payment_bp
    from admin import admin_bp
    from plans import plans_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(plans_bp)

    # Support Center — AI Chat Assistant
    try:
        from ai_chat import ai_chat_bp
        app.register_blueprint(ai_chat_bp)
        logger.info('AI chat support blueprint loaded')
    except Exception as e:
        logger.warning(f'ai_chat.py not loaded — AI assistant disabled: {e}')

    # Monthly Champion Challenge
    try:
        from challenge_routes import challenge_bp, challenge_admin_bp
        app.register_blueprint(challenge_bp)
        app.register_blueprint(challenge_admin_bp)
        logger.info('Monthly Champion Challenge blueprints loaded')
    except Exception as e:
        logger.warning(f'challenge_routes.py not loaded — challenge feature disabled: {e}')

    try:
        from referral import referral_bp
        app.register_blueprint(referral_bp)
    except ImportError:
        logger.warning('referral.py not found — skipping')

    # Cashback System
    try:
        from cashback_routes import cashback_bp, cashback_admin_bp
        app.register_blueprint(cashback_bp)
        app.register_blueprint(cashback_admin_bp)
        logger.info('Cashback System blueprints loaded')
    except Exception as e:
        logger.warning(f'cashback_routes.py not loaded — cashback feature disabled: {e}')

    # Spin & Win
    try:
        from spin_routes import spin_bp, spin_admin_bp
        app.register_blueprint(spin_bp)
        app.register_blueprint(spin_admin_bp)
        logger.info('Spin & Win blueprints loaded')
    except Exception as e:
        logger.warning(f'spin_routes.py not loaded — spin feature disabled: {e}')

    # Coupon System
    try:
        from coupon_routes import coupon_bp, coupon_admin_bp
        app.register_blueprint(coupon_bp)
        app.register_blueprint(coupon_admin_bp)
        logger.info('Coupon System blueprints loaded')
    except Exception as e:
        logger.warning(f'coupon_routes.py not loaded — coupon feature disabled: {e}')

    # Merchant Dashboard
    try:
        from merchant_routes import merchant_bp, merchant_admin_bp, merchant_api_bp
        app.register_blueprint(merchant_bp)
        app.register_blueprint(merchant_admin_bp)
        app.register_blueprint(merchant_api_bp)
        logger.info('Merchant Dashboard blueprints loaded')
    except Exception as e:
        logger.warning(f'merchant_routes.py not loaded — merchant feature disabled: {e}')

    # Virtual Dollar Card
    try:
        from card_routes import card_bp, card_admin_bp
        app.register_blueprint(card_bp)
        app.register_blueprint(card_admin_bp)
        logger.info('Virtual Dollar Card blueprints loaded')
    except Exception as e:
        logger.warning(f'card_routes.py not loaded — virtual card feature disabled: {e}')

    # Bill Reminder
    try:
        from reminder_routes import reminder_bp, reminder_admin_bp
        app.register_blueprint(reminder_bp)
        app.register_blueprint(reminder_admin_bp)
        logger.info('Bill Reminder blueprints loaded')
    except Exception as e:
        logger.warning(f'reminder_routes.py not loaded — bill reminder feature disabled: {e}')

    # Smart Price Comparison
    try:
        from comparison_routes import comparison_bp, comparison_admin_bp
        app.register_blueprint(comparison_bp)
        app.register_blueprint(comparison_admin_bp)
        logger.info('Smart Price Comparison blueprints loaded')
    except Exception as e:
        logger.warning(f'comparison_routes.py not loaded — price comparison feature disabled: {e}')

    # Gamification
    try:
        from gamification_routes import gamification_bp, gamification_admin_bp
        app.register_blueprint(gamification_bp)
        app.register_blueprint(gamification_admin_bp)
        logger.info('Gamification blueprints loaded')
    except Exception as e:
        logger.warning(f'gamification_routes.py not loaded — gamification feature disabled: {e}')

    # Public web pages — Delete Account (Google Play requirement),
    # Privacy Policy, Terms of Service
    from public_pages import public_pages_bp
    app.register_blueprint(public_pages_bp)
    logger.info('Public pages blueprint loaded (/delete-account, /privacy-policy, /terms-of-service)')

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
    def _debug_enabled():
        """Diagnostic endpoints that can spend SMS credit or reveal config are
        OFF unless DEBUG is on or ENABLE_DEBUG_ROUTES=true is set on the server."""
        return bool(app.config.get('DEBUG')) or os.getenv('ENABLE_DEBUG_ROUTES', '').strip().lower() == 'true'

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

    @app.route('/api/debug/add-challenge-percent-columns', methods=['GET'])
    def add_challenge_percent_columns():
        """
        ONE-TIME MIGRATION: adds the Top-5 percentage-reward columns to the
        existing `challenge_config` singleton row, replacing the old
        first_place_percent / second_place_bonus / third_place_bonus
        scheme (Top 3 only, mixed % + fixed-Naira) with a clean Top-5,
        all-percentage scheme (each rank keeps a % of their OWN spend).

        Visit this URL once (GET request) after deploying; it's idempotent
        (IF NOT EXISTS) and safe to call repeatedly. The DEFAULT values
        below (10/8/6/4/2) are what the existing config row will be
        back-filled with the moment this runs — matching the rates
        actually requested for this change — so no separate data-fix step
        is needed. The old columns are left in place, unused, rather than
        dropped, to avoid any risk of a destructive DROP COLUMN.
        """
        from sqlalchemy import text
        try:
            with db.engine.connect() as conn:
                statements = [
                    "ALTER TABLE challenge_config ADD COLUMN IF NOT EXISTS rank1_percent FLOAT DEFAULT 10.0",
                    "ALTER TABLE challenge_config ADD COLUMN IF NOT EXISTS rank2_percent FLOAT DEFAULT 8.0",
                    "ALTER TABLE challenge_config ADD COLUMN IF NOT EXISTS rank3_percent FLOAT DEFAULT 6.0",
                    "ALTER TABLE challenge_config ADD COLUMN IF NOT EXISTS rank4_percent FLOAT DEFAULT 4.0",
                    "ALTER TABLE challenge_config ADD COLUMN IF NOT EXISTS rank5_percent FLOAT DEFAULT 2.0",
                ]
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            return jsonify({
                'status': 'success',
                'message': 'Challenge percent columns added (or already existed): '
                           'rank1=10%, rank2=8%, rank3=6%, rank4=4%, rank5=2%.'
            })
        except Exception as e:
            logger.error(f'add_challenge_percent_columns error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/debug/add-pin-columns', methods=['GET'])
    def add_pin_columns():
        """
        ONE-TIME MIGRATION: adds the server-side Login PIN columns (and
        lockout/audit columns for both PIN types) to the existing `users`
        table, plus the Termii message_id column on `otps`.

        db.create_all() only creates tables that don't exist yet — it never
        ALTERs an existing table — so this has to be run manually once after
        deploying the PIN-persistence changes. Visit this URL once (GET
        request) after deploying; it's idempotent (IF NOT EXISTS) and safe
        to call repeatedly. Existing users/rows are untouched — every new
        column is nullable or defaults to 0, so no existing data is reset.
        """
        from sqlalchemy import text
        try:
            with db.engine.connect() as conn:
                statements = AUTH_COLUMN_MIGRATIONS
                for stmt in statements:
                    conn.execute(text(stmt))
                conn.commit()
            return jsonify({
                'status': 'success',
                'message': 'PIN columns added (or already existed). Existing users/PINs untouched.'
            })
        except Exception as e:
            logger.error(f'add_pin_columns error: {e}')
            return jsonify({'status': 'error', 'message': str(e)}), 500

    @app.route('/api/debug/fix-referral-bonus', methods=['GET'])   
    def fix_referral_bonus():
        """
        Force-pay ₦50 referral bonus to a specific referrer
        for ALL users they referred, regardless of referral_bonus_claimed flag.
        """
        # UNAUTHENTICATED endpoint that credits referral balances (and, without
        # a referrer_id, lists referrers' names/emails/balances) — it must never
        # be reachable on a live server unless you deliberately switch it on.
        if not _debug_enabled():
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
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
        if not _debug_enabled():
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
        api_key = app.config.get('TERMII_API_KEY', '').strip()
        db_url  = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        ps_key  = app.config.get('PAYSTACK_SECRET_KEY', '').strip()
        return jsonify({
            'TERMII_API_KEY':      (api_key[:6]+'...'+api_key[-4:]) if len(api_key)>10 else 'NOT_SET',
            'TERMII_SENDER_ID':    app.config.get('TERMII_SENDER_ID'),
            'PAYSTACK_SECRET_KEY': (ps_key[:8]+'...') if ps_key else 'NOT_SET',
            'DB_URL_PREVIEW':      db_url[:50]+'...' if len(db_url)>50 else db_url,
            'DB_TYPE':             'postgresql' if 'postgresql' in db_url else 'sqlite',
            # What the app sees for the caller: if client_ip_seen is the same
            # for every phone/network you test from, rate limits are still being
            # shared and ProxyFix's x_for needs adjusting for your proxy chain.
            'client_ip_seen':      request.remote_addr,
            'x_forwarded_for':     request.headers.get('X-Forwarded-For'),
        })

    @app.route('/api/debug/test-sms', methods=['GET', 'POST'])
    def debug_test_sms():
        """
        Sends a test SMS through the SAME code path OTPs use (utils.send_sms),
        so it reflects what real users get. Returns which attempt worked, or
        Termii's exact error for each attempt that didn't.
        """
        if not _debug_enabled():
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
        if request.method == 'POST':
            phone_raw = (request.get_json(silent=True) or {}).get('phone', '')
        else:
            phone_raw = request.args.get('phone', '')
        if not phone_raw:
            return jsonify({'error': 'phone is required'}), 400
        if not app.config.get('TERMII_API_KEY', '').strip():
            return jsonify({'error': 'TERMII_API_KEY not set'}), 500
        from utils import send_sms
        diagnostics = {}
        sent, message_id, error = send_sms(phone_raw, 'Cheap4u test message. Ignore.', diagnostics=diagnostics)
        return jsonify({
            'result':     'SMS_SENT' if sent else 'ALL_FAILED',
            'message_id': message_id,
            'error':      error,
            'attempts':   diagnostics,
        }), (200 if sent else 500)

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

    # ── One-time DB/table/plan initialization ──────────────────────────
    # This used to run inside @app.before_request, which meant whichever
    # user's request happened to be first after every cold start (very
    # often the Android app's own /health check right after waking a
    # sleeping Render instance) got stuck waiting for db.create_all(),
    # a schema-migration ALTER TABLE, init_plans's ~40+ idempotency
    # SELECT/INSERT round-trips, AND the startup of three background
    # schedulers — all before that user got any response at all.
    #
    # It's moved here so it runs exactly once, during process boot
    # (i.e. as part of Render/gunicorn's own startup sequence), instead
    # of inside the response path of a real user's request. Every
    # operation inside is already idempotent (db.create_all() only
    # creates missing tables, the ALTER TABLE uses IF NOT EXISTS,
    # init_plans checks for existing rows before inserting, and each
    # scheduler guards itself with a module-level "already started"
    # flag) — so it's safe to run every time a worker boots.
    def _init_db_and_extras():
        if getattr(app, '_tables_created', False):
            return
        try:
            db.create_all()
            # db.create_all() only creates tables that don't exist yet —
            # it does NOT add new columns to a table that was already
            # created before the column existed in the model (e.g.
            # support_chat_messages.action, added when the AI Assistant
            # feature was upgraded). Patch those in directly so an
            # older, already-deployed database self-heals on the next
            # boot instead of every /api/chat call failing with
            # "column ... does not exist".
            try:
                with db.engine.connect() as conn:
                    conn.execute(db.text(
                        "ALTER TABLE support_chat_messages "
                        "ADD COLUMN IF NOT EXISTS action VARCHAR(40)"
                    ))
                    conn.commit()
                logger.info('✅ Verified support_chat_messages.action column')
            except Exception as e:
                logger.warning(f'Column migration check failed (non-fatal): {e}')
            # Same self-healing for the PIN + OTP columns (see
            # AUTH_COLUMN_MIGRATIONS). Without this, a database that predates
            # them breaks every OTP/login query until someone remembers to
            # visit /api/debug/add-pin-columns by hand after deploying.
            if db.engine.dialect.name == 'postgresql':
                for stmt in AUTH_COLUMN_MIGRATIONS:
                    try:
                        with db.engine.begin() as conn:
                            conn.execute(db.text(stmt))
                    except Exception as e:
                        logger.warning(f'Auth column migration skipped ({stmt[:70]}...): {e}')
            try:
                from init_plans import init_all
                init_all()
            except Exception as e:
                logger.warning(f'init_plans error (non-fatal): {e}')
            app._tables_created = True
            logger.info('✅ DB tables ready')

            try:
                from challenge import start_scheduler
                start_scheduler(app)
            except Exception as e:
                logger.warning(f'Challenge scheduler not started (non-fatal): {e}')

            try:
                from cashback import start_scheduler as start_cashback_scheduler
                start_cashback_scheduler(app)
            except Exception as e:
                logger.warning(f'Cashback scheduler not started (non-fatal): {e}')

            try:
                from reminder import start_scheduler as start_reminder_scheduler
                start_reminder_scheduler(app)
            except Exception as e:
                logger.warning(f'Bill Reminder scheduler not started (non-fatal): {e}')
        except Exception as e:
            logger.error(f'DB init error: {e}')

    # Run it now, as part of app boot, so it's already done long before
    # gunicorn starts accepting real traffic.
    with app.app_context():
        _init_db_and_extras()

    # Lightweight self-healing fallback ONLY: if the database genuinely
    # wasn't reachable yet at boot (e.g. DNS/Postgres not ready), this
    # retries the (still-idempotent) init on the next request instead of
    # leaving the app permanently uninitialized. In the normal case
    # (init already succeeded above), this is a single fast attribute
    # check on every request — no DB work, no added latency.
    @app.before_request
    def _ensure_db_initialized():
        if not getattr(app, '_tables_created', False):
            _init_db_and_extras()

    return app


app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
