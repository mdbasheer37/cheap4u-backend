# auth.py — Fixed: SMS failure returns 200, DVA created on registration
import bcrypt
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from models import db, User, OTP
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
        user_id    = user.id,
        email      = user.email,
        phone      = user.phone,
        code       = otp_code,
        purpose    = purpose,
        expires_at = expires_at,
    )
    db.session.add(otp)
    db.session.commit()
    message  = f"Your Cheap4u verification code is {otp_code}. It expires in 10 minutes. Do not share."
    sms_sent = send_sms(user.phone, message)
    return sms_sent, otp_code


def _setup_paystack(user, name, email, phone):
    """
    Create Paystack customer + Dedicated Virtual Account.
    Runs in a background thread so it never blocks registration.
    Returns immediately — DVA is created asynchronously.
    """
    import threading

    def _do_setup():
        try:
            from payment import create_paystack_customer, create_dedicated_virtual_account
            from app import app

            with app.app_context():
                # Re-fetch user inside thread context
                u = User.query.get(user.id)
                if not u:
                    return

                # Skip if already done
                if u.paystack_customer_code:
                    current_app.logger.info(
                        f'Paystack already set up for user {u.id}'
                    )
                    return

                name_parts = name.strip().split(' ', 1)
                first_name = name_parts[0]
                last_name  = name_parts[1] if len(name_parts) > 1 else ''

                # Step 1: Create customer
                customer_code = create_paystack_customer(
                    email, first_name, last_name, phone
                )
                u.paystack_customer_code = customer_code
                db.session.commit()
                current_app.logger.info(
                    f'Paystack customer created for user {u.id}: {customer_code}'
                )

                # Step 2: Create DVA
                dva = create_dedicated_virtual_account(customer_code)
                if dva:
                    u.virtual_account_number = dva['account_number']
                    u.virtual_bank_name      = dva['bank_name']
                    u.virtual_account_name   = dva['account_name']
                    db.session.commit()
                    current_app.logger.info(
                        f'DVA created for user {u.id}: '
                        f'{dva["bank_name"]} {dva["account_number"]}'
                    )
                else:
                    current_app.logger.warning(
                        f'DVA not available for user {u.id} '
                        f'(account not live or bank unavailable)'
                    )

        except Exception as e:
            current_app.logger.error(
                f'Paystack setup error for user {user.id}: {e}'
            )

    t = threading.Thread(target=_do_setup, daemon=True)
    t.start()


# ── Set transaction PIN ──────────────────────────────────────────────
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

    user.transaction_pin_hash = bcrypt.hashpw(
        new_pin.encode(), bcrypt.gensalt()
    ).decode()
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Transaction PIN set successfully'})


# ── Register ─────────────────────────────────────────────────────────
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
        return jsonify({'status': 'error',
                        'message': 'Password must be at least 6 characters'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'Email already registered'}), 400
    if User.query.filter_by(phone=phone).first():
        return jsonify({'status': 'error',
                        'message': 'Phone number already registered'}), 400

    referrer = None
    if ref_code:
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if not referrer:
            return jsonify({'status': 'error',
                            'message': 'Invalid referral code'}), 400

    # Create user
    user = User(
        name          = name,
        email         = email,
        phone         = phone,
        referral_code = generate_referral_code(),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()

    if referrer and referrer.id != user.id:
        user.referred_by         = ref_code
        user.referred_by_user_id = referrer.id
        referrer.total_referrals += 1

    db.session.commit()

    # Start Paystack setup in background thread (non-blocking)
    # DVA will be ready within a few seconds after registration
    _setup_paystack(user, name, email, phone)

    # Send OTP
    sms_sent, _ = _send_otp(user, purpose='registration')

    if not sms_sent:
        current_app.logger.error(f'OTP SMS failed for {phone}')
        # FIXED: Return 200 success — account IS created, user can tap Resend OTP
        return jsonify({
            'status':  'success',
            'message': 'Account created. SMS failed — tap Resend OTP to get your code.',
            'data':    {'user_id': user.id, 'phone': phone, 'sms_failed': True},
        })

    return jsonify({
        'status':  'success',
        'message': 'Registration successful. OTP sent to your phone.',
        'data':    {'user_id': user.id, 'phone': phone},
    })


# ── Verify OTP ───────────────────────────────────────────────────────
@auth_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    data     = request.get_json() or {}
    user_id  = data.get('user_id')
    otp_code = data.get('otp_code')

    if not user_id or not otp_code:
        return jsonify({'status': 'error',
                        'message': 'user_id and otp_code required'}), 400

    otp = OTP.query.filter_by(
        user_id = user_id,
        code    = otp_code,
        is_used = False,
        purpose = 'registration',
    ).first()

    if not otp:
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        return jsonify({'status': 'error',
                        'message': 'OTP has expired. Please request a new one.'}), 400

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


# ── Resend OTP ───────────────────────────────────────────────────────
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
        return jsonify({
            'status':  'error',
            'message': 'Please wait 60 seconds before requesting a new OTP'
        }), 429

    sms_sent, _ = _send_otp(user, purpose='registration')
    if not sms_sent:
        return jsonify({
            'status':  'error',
            'message': 'Could not send OTP. Check your phone number.'
        }), 500

    return jsonify({'status': 'success', 'message': 'OTP resent to your phone.'})


# ── Login ─────────────────────────────────────────────────────────────
@auth_bp.route('/login', methods=['POST'])
def login():
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not email or not password:
        return jsonify({'status': 'error',
                        'message': 'Email and password required'}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401

    if not user.is_active:
        return jsonify({'status': 'error',
                        'message': 'Account is blocked. Contact support.'}), 403

    if not user.is_verified:
        sms_sent, _ = _send_otp(user, purpose='registration')
        return jsonify({
            'status':  'error',
            'message': 'Account not verified. OTP sent to your phone.',
            'requires_verification': True,
            'user_id': user.id,
            'phone':   user.phone,
        }), 403

    # If user has no DVA yet, try again in background on login
    if not user.virtual_account_number:
        _setup_paystack(user, user.name, user.email, user.phone)

    user.last_login = datetime.utcnow()
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify({
        'status':  'success',
        'message': 'Login successful',
        'data':    {'user': user.to_dict(), 'session_token': token},
    })


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
        return jsonify({'status': 'error',
                        'message': 'No PIN set. Please set a PIN first.'}), 400

    if bcrypt.checkpw(pin.encode(), user.transaction_pin_hash.encode()):
        return jsonify({'status': 'success', 'message': 'PIN verified'})
    return jsonify({'status': 'error', 'message': 'Incorrect PIN'}), 401
