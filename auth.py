# auth.py — Fixed: DVA in background thread + referral bonus display
import bcrypt
import threading
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from models import db, User, OTP
import gamification as gamification_service
from utils import generate_referral_code, generate_otp, send_sms, validate_email, validate_phone
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
_otp_last_sent = {}


def invalidate_existing_otps(user_id):
    OTP.query.filter_by(user_id=user_id, is_used=False).update({'is_used': True})
    db.session.commit()


def can_resend_otp(user_id):
    last = _otp_last_sent.get(user_id)
    if last and (datetime.utcnow() - last).total_seconds() < 60:
        return False
    _otp_last_sent[user_id] = datetime.utcnow()
    return True


def _send_otp(user, purpose='registration'):
    invalidate_existing_otps(user.id)
    otp_code   = generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    otp = OTP(
        user_id=user.id, email=user.email, phone=user.phone,
        code=otp_code, purpose=purpose, expires_at=expires_at,
    )
    db.session.add(otp)
    db.session.commit()
    message  = f"Your Cheap4u verification code is {otp_code}. Valid for 10 minutes. Do not share."
    sms_sent = send_sms(user.phone, message)
    return sms_sent, otp_code


def _setup_paystack_background(app, user_id, name, email, phone):
    """
    Create Paystack customer + DVA in background thread.
    FIX: captures app object BEFORE starting thread, uses explicit app_context inside.
    """
    def _run():
        with app.app_context():
            try:
                from payment import create_paystack_customer, create_dedicated_virtual_account
                u = User.query.get(user_id)
                if not u or u.paystack_customer_code:
                    return

                name_parts = name.strip().split(' ', 1)
                first_name = name_parts[0]
                last_name  = name_parts[1] if len(name_parts) > 1 else ''

                customer_code = create_paystack_customer(email, first_name, last_name, phone)
                u.paystack_customer_code = customer_code
                db.session.commit()
                app.logger.info(f'Paystack customer created for user {user_id}')

                dva = create_dedicated_virtual_account(customer_code)
                if dva:
                    u.virtual_account_number = dva['account_number']
                    u.virtual_bank_name      = dva['bank_name']
                    u.virtual_account_name   = dva['account_name']
                    db.session.commit()
                    app.logger.info(
                        f'DVA created for user {user_id}: '
                        f'{dva["bank_name"]} {dva["account_number"]}'
                    )
                else:
                    app.logger.warning(f'DVA not available for user {user_id}')
            except Exception as e:
                app.logger.error(f'Paystack setup error for user {user_id}: {e}')

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ── Register ──────────────────────────────────────────────────────────
@auth_bp.route('/register', methods=['POST'])
def register():
    data     = request.get_json() or {}
    name     = (data.get('name') or '').strip()
    email    = (data.get('email') or '').strip().lower()
    phone    = (data.get('phone') or '').strip()
    password = data.get('password') or ''
    ref_code = (data.get('referral_code') or '').strip()

    if not all([name, email, phone, password]):
        return jsonify({'status': 'error', 'message': 'All fields are required'}), 400
    if not validate_email(email):
        return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400
    if not validate_phone(phone):
        return jsonify({'status': 'error', 'message': 'Phone must be 11 digits'}), 400
    if len(password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'Email already registered'}), 400
    if User.query.filter_by(phone=phone).first():
        return jsonify({'status': 'error', 'message': 'Phone number already registered'}), 400

    referrer = None
    if ref_code:
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if not referrer:
            return jsonify({'status': 'error', 'message': 'Invalid referral code'}), 400

    user = User(
        name=name, email=email, phone=phone,
        referral_code=generate_referral_code(),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    if referrer and referrer.id != user.id:
        user.referred_by         = ref_code
        user.referred_by_user_id = referrer.id
        referrer.total_referrals = (referrer.total_referrals or 0) + 1

    db.session.commit()

    # FIX: run Paystack setup in background thread — never blocks registration
    app = current_app._get_current_object()
    _setup_paystack_background(app, user.id, name, email, phone)

    # Send OTP — return 200 even if SMS fails
    sms_sent, _ = _send_otp(user, purpose='registration')
    if not sms_sent:
        current_app.logger.error(f'OTP SMS failed for {phone}')
        return jsonify({
            'status':  'success',
            'message': 'Account created. SMS failed — tap Resend OTP on next screen.',
            'data':    {'user_id': user.id, 'phone': phone, 'sms_failed': True},
        })

    return jsonify({
        'status':  'success',
        'message': 'Registration successful. OTP sent to your phone.',
        'data':    {'user_id': user.id, 'phone': phone},
    })


# ── Verify OTP ────────────────────────────────────────────────────────
@auth_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    data     = request.get_json() or {}
    user_id  = data.get('user_id')
    otp_code = data.get('otp_code')
    if not user_id or not otp_code:
        return jsonify({'status': 'error', 'message': 'user_id and otp_code required'}), 400

    otp = OTP.query.filter_by(
        user_id=user_id, code=otp_code, is_used=False, purpose='registration'
    ).first()
    if not otp:
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        return jsonify({'status': 'error', 'message': 'OTP expired. Request a new one.'}), 400

    otp.is_used      = True
    user             = User.query.get(user_id)
    user.is_verified = True
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify({
        'status':  'success',
        'message': 'Account verified successfully',
        'data':    {'user': user.to_dict(), 'session_token': token},
    })


# ── Resend OTP ────────────────────────────────────────────────────────
@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    data    = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id required'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if not can_resend_otp(user_id):
        return jsonify({'status': 'error', 'message': 'Wait 60 seconds before requesting another OTP'}), 429
    sms_sent, _ = _send_otp(user, purpose='registration')
    if not sms_sent:
        return jsonify({'status': 'error', 'message': 'Could not send OTP. Check your phone number.'}), 500
    return jsonify({'status': 'success', 'message': 'OTP resent to your phone.'})


# ── Login ─────────────────────────────────────────────────────────────
@auth_bp.route('/login', methods=['POST'])
def login():
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return jsonify({'status': 'error', 'message': 'Email and password required'}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401
    if not user.is_active:
        return jsonify({'status': 'error', 'message': 'Account is blocked. Contact support.'}), 403
    if not user.is_verified:
        sms_sent, _ = _send_otp(user, purpose='registration')
        return jsonify({
            'status': 'error', 'message': 'Account not verified. OTP sent to your phone.',
            'requires_verification': True, 'user_id': user.id, 'phone': user.phone,
        }), 403

    # Retry DVA if missing
    if not user.virtual_account_number:
        app = current_app._get_current_object()
        _setup_paystack_background(app, user.id, user.name, user.email, user.phone)

    gamification_service.record_daily_login(user)   # must run BEFORE last_login is overwritten below
    user.last_login = datetime.utcnow()
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify({
        'status':  'success',
        'message': 'Login successful',
        'data':    {'user': user.to_dict(), 'session_token': token},
    })


# ── Set PIN ───────────────────────────────────────────────────────────
@auth_bp.route('/set-pin', methods=['POST'])
@jwt_required()
def set_transaction_pin():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data    = request.get_json() or {}
    old_pin = data.get('old_pin')
    new_pin = data.get('new_pin')
    if old_pin and user.transaction_pin_hash:
        if not bcrypt.checkpw(old_pin.encode(), user.transaction_pin_hash.encode()):
            return jsonify({'status': 'error', 'message': 'Incorrect current PIN'}), 401
    if not new_pin or not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
        return jsonify({'status': 'error', 'message': 'PIN must be 4-6 digits'}), 400
    user.transaction_pin_hash = bcrypt.hashpw(new_pin.encode(), bcrypt.gensalt()).decode()
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'PIN set successfully'})


# ── Verify PIN ────────────────────────────────────────────────────────
@auth_bp.route('/verify-pin', methods=['POST'])
@jwt_required()
def verify_pin():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = request.get_json() or {}
    pin  = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'PIN required'}), 400
    if not user.transaction_pin_hash:
        return jsonify({'status': 'error', 'message': 'No PIN set. Please set a PIN first.'}), 400
    if bcrypt.checkpw(pin.encode(), user.transaction_pin_hash.encode()):
        return jsonify({'status': 'success', 'message': 'PIN verified'})
    return jsonify({'status': 'error', 'message': 'Incorrect PIN'}), 401


# ── Forgot Password ───────────────────────────────────────────────────
@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    user  = None
    if email:
        user = User.query.filter_by(email=email).first()
    elif phone:
        user = User.query.filter_by(phone=phone).first()
    if not user:
        return jsonify({'status': 'success', 'message': 'If this account exists, an OTP has been sent.'})
    if not can_resend_otp(user.id):
        return jsonify({'status': 'error', 'message': 'Wait 60 seconds before requesting another OTP.'}), 429
    sms_sent, _ = _send_otp(user, purpose='password_reset')
    if not sms_sent:
        return jsonify({'status': 'error', 'message': 'Could not send OTP. Check your phone number.'}), 500
    return jsonify({
        'status':  'success',
        'message': f'OTP sent to your phone.',
        'data':    {'user_id': user.id, 'phone': user.phone},
    })


# ── Reset Password ────────────────────────────────────────────────────
@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data         = request.get_json() or {}
    user_id      = data.get('user_id')
    otp_code     = data.get('otp_code', '').strip()
    new_password = data.get('new_password', '').strip()
    if not all([user_id, otp_code, new_password]):
        return jsonify({'status': 'error', 'message': 'user_id, otp_code and new_password required'}), 400
    if len(new_password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters'}), 400
    otp = OTP.query.filter_by(
        user_id=user_id, code=otp_code, is_used=False, purpose='password_reset'
    ).first()
    if not otp:
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        return jsonify({'status': 'error', 'message': 'OTP expired. Request a new one.'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    user.set_password(new_password)
    otp.is_used = True
    db.session.commit()
    token = create_access_token(identity=str(user.id))
    return jsonify({
        'status':  'success',
        'message': 'Password reset successfully!',
        'data':    {'session_token': token, 'user': user.to_dict()},
    })
