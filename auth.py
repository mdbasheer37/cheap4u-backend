# auth.py — Fixed: DVA in background thread + referral bonus display
#
# CHANGES (OTP reliability + server-side PIN persistence):
#   - can_resend_otp() is now DB-backed (was an in-memory dict — lost on
#     every restart/deploy, and unsafe if this ever runs with >1 worker).
#   - verify_otp() now locks the OTP row with_for_update() so two
#     simultaneous verify requests for the same code can't both succeed.
#   - _send_otp() captures Termii's message_id and returns an honest
#     "accepted, not confirmed delivered" message (see utils.send_sms).
#   - NEW: login_pin_hash on the User is now the source of truth for the
#     "quick PIN" login (previously stored ONLY on-device — see
#     set-login-pin / login-with-pin / reset-pin below).
#   - NEW: PIN lockout after repeated wrong attempts (models.py).
#
# OTP FIXES (this revision):
#   - `logger` was used in _setup_paystack_sync() but never defined, so every
#     registration crashed with NameError (also inside the except handler) and
#     returned 500 BEFORE the OTP was ever sent. Defined below.
#   - Paystack (customer + DVA, up to 3 x 30s calls) no longer runs in front of
#     the OTP send. It starts in parallel and register() waits only a few
#     seconds for it.
#   - Resending an OTP re-sends the SAME still-valid code instead of rotating it,
#     so a slow first SMS (common at night) is not invalidated by the resend.
#   - A sign-up with a mistyped phone number can be corrected ("Change
#     Email/Phone") instead of being stuck behind "already registered".
#   - Login of an unverified account respects the resend cooldown.
#   - OTP input is normalised to digits (spaces/dashes from paste are ignored).
import logging
import math
import re
import threading
import bcrypt
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from sqlalchemy import func
from models import db, User, OTP
import gamification as gamification_service
from utils import generate_referral_code, generate_otp, send_sms, validate_email, validate_phone
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from extensions import limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_EXPIRY_MINUTES = 10
# A resend re-uses the current code only while it still has at least this long
# to live (otherwise a fresh code is issued, so the SMS never promises a
# lifetime of a few seconds).
OTP_MIN_REUSE_SECONDS = 120
# How long register() waits for the Paystack account to be ready before it
# answers anyway. The account keeps being created in the background.
PAYSTACK_WAIT_SECONDS = 6

# Fixed dummy hash used to keep bcrypt.checkpw() timing similar whether or
# not an account/PIN exists, so a login-with-pin request can't be used to
# probe which phone numbers/emails have accounts.
_DUMMY_HASH = bcrypt.hashpw(b'not-a-real-pin', bcrypt.gensalt()).decode('utf-8')


def _body_key(*fields):
    """
    Rate-limit key for the OTP endpoints: the ACCOUNT being targeted (taken from
    the JSON body), falling back to the client IP when it isn't there.

    Per-IP limits are the wrong tool here: behind a proxy they can end up shared
    by every user (one person's resends then locked everyone else out of their
    OTP), and they do nothing against someone guessing a single account's code
    from many IPs. Keying on the account fixes both — each account gets its own
    small allowance of attempts, which also caps code-guessing per account.
    """
    def _key():
        data = request.get_json(silent=True) or {}
        for f in fields:
            v = data.get(f)
            if v not in (None, ''):
                return f"{f}:{str(v).strip().lower()}"
        return get_remote_address()
    return _key


def _clean_code(value):
    """Keep digits only — users paste codes like '123 456' or '123-456', and a
    JSON client may send the code as a number rather than a string."""
    return re.sub(r'\D', '', str(value if value is not None else ''))


def invalidate_existing_otps(user_id, purpose=None):
    q = OTP.query.filter_by(user_id=user_id, is_used=False)
    if purpose:
        q = q.filter_by(purpose=purpose)
    q.update({'is_used': True})
    db.session.commit()


def can_resend_otp(user_id, purpose='registration'):
    """
    DB-backed cooldown check (replaces the old in-memory dict, which reset
    on every deploy/restart and would not be safe if this service ever runs
    with more than one worker process).
    """
    last = (
        OTP.query
        .filter_by(user_id=user_id, purpose=purpose)
        .order_by(OTP.created_at.desc())
        .first()
    )
    if last and (datetime.utcnow() - last.created_at).total_seconds() < OTP_RESEND_COOLDOWN_SECONDS:
        return False
    return True


def _is_reclaimable(u):
    """
    An existing account a new sign-up may take over: unverified (its phone number
    was never proven) AND it has never held any money. Anything else keeps
    blocking the email/phone exactly as before.
    """
    return (
        not u.is_verified
        and not (u.wallet_balance or 0)
        and not (u.referral_balance or 0)
    )


def _active_otp(user, purpose):
    """Newest unused, unexpired OTP that was issued for the user's CURRENT phone."""
    return (
        OTP.query
        .filter_by(user_id=user.id, purpose=purpose, is_used=False, phone=user.phone)
        .filter(OTP.expires_at > datetime.utcnow())
        .order_by(OTP.created_at.desc())
        .first()
    )


def _send_otp(user, purpose='registration'):
    """
    Issues an OTP and sends it via Termii, recording the provider's
    message_id when given.

    If a still-valid code already exists for this user+purpose+phone, that SAME
    code is sent again (with its original expiry) rather than a new one being
    generated. Rotating the code on every resend meant a slow first SMS —
    which is exactly when people tap Resend — arrived already invalidated and
    was rejected as "Invalid OTP code". A code for a different phone number is
    never reused.

    Returns (sms_sent: bool, otp_code: str, user_message: str).
    """
    now      = datetime.utcnow()
    existing = _active_otp(user, purpose)
    if existing and (existing.expires_at - now).total_seconds() >= OTP_MIN_REUSE_SECONDS:
        otp_code   = existing.code
        expires_at = existing.expires_at
    else:
        otp_code   = generate_otp()
        expires_at = now + timedelta(minutes=OTP_EXPIRY_MINUTES)

    # One live row per user+purpose. The new row gets a fresh created_at, so
    # the resend cooldown (can_resend_otp) keeps measuring from the last send.
    invalidate_existing_otps(user.id, purpose=purpose)
    otp = OTP(
        user_id=user.id, email=user.email, phone=user.phone,
        code=otp_code, purpose=purpose, expires_at=expires_at,
    )
    db.session.add(otp)
    db.session.commit()

    minutes_left = max(1, math.ceil((expires_at - now).total_seconds() / 60))
    message = f"Your Cheap4u verification code is {otp_code}. Valid for {minutes_left} minutes. Do not share."
    sms_sent, message_id, error = send_sms(user.phone, message)

    if message_id:
        otp.provider_message_id = message_id
    if not sms_sent:
        # Nothing was accepted for delivery, so don't make the user sit out the
        # resend cooldown for an SMS that never went out: back-date the row so
        # Resend works straight away (it re-sends this same still-valid code).
        otp.created_at = now - timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS)
    if message_id or not sms_sent:
        db.session.commit()

    if sms_sent:
        user_message = ("OTP has been sent. If you don't receive it shortly, "
                         "please wait before requesting another OTP.")
    else:
        current_app.logger.error(f"OTP send failed for user {user.id} ({purpose}): {error}")
        user_message = "We couldn't send the OTP right now. Please try again in a moment."

    return sms_sent, otp_code, user_message


def _setup_paystack_sync(user_id, name, email, phone):
    """
    Creates the Paystack customer + Dedicated Virtual Account for a user,
    synchronously, in the CALLING request/thread. Returns True if a DVA now
    exists for this user (either just created, or already existed), False
    if it genuinely couldn't be created (Paystack down, DVA feature not
    enabled on this account, etc.) — never raises, so a Paystack hiccup
    never blocks the caller.
    """
    try:
        from payment import create_paystack_customer, create_dedicated_virtual_account
        u = User.query.get(user_id)
        if not u:
            return False
        if u.virtual_account_number:
            return True   # already has one — nothing to do
        if not u.paystack_customer_code:
            name_parts = name.strip().split(' ', 1)
            first_name = name_parts[0]
            last_name  = name_parts[1] if len(name_parts) > 1 else ''
            u.paystack_customer_code = create_paystack_customer(email, first_name, last_name, phone)
            db.session.commit()
            logger.info(f'Paystack customer created for user {user_id}')

        dva = create_dedicated_virtual_account(u.paystack_customer_code)
        if dva:
            u.virtual_account_number = dva['account_number']
            u.virtual_bank_name      = dva['bank_name']
            u.virtual_account_name   = dva['account_name']
            db.session.commit()
            logger.info(f'DVA created for user {user_id}: {dva["bank_name"]} {dva["account_number"]}')
            return True
        logger.warning(f'DVA not available for user {user_id}')
        return False
    except Exception as e:
        logger.error(f'Paystack setup error for user {user_id}: {e}')
        return False


def _setup_paystack_background(app, user_id, name, email, phone):
    """
    Runs Paystack customer + DVA creation in a background thread and returns
    the started Thread. register() starts it in parallel with the OTP SMS and
    waits only briefly (see PAYSTACK_WAIT_SECONDS) so a slow Paystack can never
    delay or lose the OTP; /login uses it to retry for users who still don't
    have a DVA.
    """
    def _run():
        with app.app_context():
            _setup_paystack_sync(user_id, name, email, phone)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _user_by_email(email):
    """
    Case-insensitive email lookup. Every current code path lowercases the
    email before storing it (see register() below), so this shouldn't
    matter for accounts created by the current code — but it's cheap
    insurance against any account created by an older version of this
    codebase that might have stored a mixed-case email, which a
    case-sensitive filter_by(email=...) would then never match against a
    lowercased login attempt (Postgres string equality is case-sensitive
    by default). A user typing their password correctly but still getting
    "Invalid credentials" is exactly the symptom that mismatch produces.
    """
    return User.query.filter(func.lower(User.email) == email.lower()).first()


def _find_user_by_identifier(identifier):
    """Look up a user by email or Nigerian local phone number."""
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    if '@' in identifier:
        return _user_by_email(identifier)
    return User.query.filter_by(phone=identifier).first()


def _lock_message(seconds):
    minutes = max(1, seconds // 60)
    return f"Too many incorrect attempts. Try again in {minutes} minute(s)."


# ── Register ──────────────────────────────────────────────────────────
@auth_bp.route('/register', methods=['POST'])
@limiter.limit("10 per hour")
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
        return jsonify({'status': 'error', 'message': 'Enter a valid Nigerian phone number (e.g. 080XXXXXXXX)'}), 400
    if len(password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters'}), 400
    existing_email = _user_by_email(email)
    existing_phone = User.query.filter_by(phone=phone).first()

    # A VERIFIED account (or one that has ever held money) always blocks a new
    # sign-up with the same details.
    if existing_email and not _is_reclaimable(existing_email):
        return jsonify({'status': 'error', 'message': 'Email already registered'}), 400
    if existing_phone and not _is_reclaimable(existing_phone):
        return jsonify({'status': 'error', 'message': 'Phone number already registered'}), 400

    # An UNVERIFIED, empty leftover is almost always a sign-up whose OTP never
    # reached the person (phone number mistyped, SMS lost). The OTP screen offers
    # "Change Email/Phone", which used to dead-end on "already registered" —
    # burning the email for good. Such an account cannot log in or spend anything,
    # so a new sign-up can safely take it over; the new owner still has to prove
    # the phone number with an OTP before the account becomes usable.
    stale = existing_email or existing_phone
    if existing_email and existing_phone and existing_email.id != existing_phone.id:
        return jsonify({
            'status': 'error',
            'message': 'That email or phone number is already tied to another unfinished sign-up. '
                       'Please double-check your details or contact support.',
        }), 400

    referrer = None
    if ref_code:
        referrer = User.query.filter_by(referral_code=ref_code).first()
        if not referrer:
            return jsonify({'status': 'error', 'message': 'Invalid referral code'}), 400

    if stale:
        user       = stale
        user.name  = name
        user.email = email
        user.phone = phone
        user.set_password(password)
        # Credit a referral only once per account.
        if referrer and referrer.id != user.id and not user.referred_by_user_id:
            user.referred_by         = ref_code
            user.referred_by_user_id = referrer.id
            referrer.total_referrals = (referrer.total_referrals or 0) + 1
    else:
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

    # Paystack customer + Dedicated Virtual Account: started in a background
    # thread RIGHT NOW so it runs in parallel with the OTP SMS below, instead of
    # in front of it. It used to run inline first — up to three 30s Paystack
    # calls — so a slow Paystack delayed the OTP by up to 90s, and because the
    # app only waits 60s the sign-up looked failed even though the account
    # existed (retrying then hit "Email already registered").
    app = current_app._get_current_object()
    paystack_thread = _setup_paystack_background(app, user.id, name, email, phone)

    # Send OTP — return 200 even if SMS fails (user can tap Resend)
    sms_sent, _, otp_message = _send_otp(user, purpose='registration')

    # Give Paystack a few seconds so the account number is normally ready by
    # the time the user has typed the code, but never hold the response (and
    # the OTP screen) hostage to it. If it isn't done, it keeps going in the
    # background and /login retries it if it ever fails.
    paystack_thread.join(timeout=PAYSTACK_WAIT_SECONDS)

    return jsonify({
        'status':  'success',
        'message': f'Account created. {otp_message}',
        'data':    {'user_id': user.id, 'phone': phone, 'sms_failed': not sms_sent},
    })


# ── Verify OTP ────────────────────────────────────────────────────────
@auth_bp.route('/verify-otp', methods=['POST'])
@limiter.limit("10 per 10 minutes", key_func=_body_key('user_id'))
def verify_otp():
    data     = request.get_json() or {}
    user_id  = data.get('user_id')
    otp_code = _clean_code(data.get('otp_code'))
    if not user_id or not otp_code:
        return jsonify({'status': 'error', 'message': 'user_id and otp_code required'}), 400

    # Row-locked so two simultaneous requests with the same code can't both
    # succeed (matches the with_for_update() pattern already used for wallet
    # updates elsewhere in this backend).
    otp = (
        OTP.query
        .filter_by(user_id=user_id, code=otp_code, is_used=False, purpose='registration')
        .with_for_update()
        .first()
    )
    if not otp:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        db.session.rollback()
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
@limiter.limit("5 per 10 minutes", key_func=_body_key('user_id'))
def resend_otp():
    data    = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id required'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if not can_resend_otp(user_id, purpose='registration'):
        return jsonify({'status': 'error', 'message': f'Please wait {OTP_RESEND_COOLDOWN_SECONDS} seconds before requesting another OTP'}), 429
    sms_sent, _, otp_message = _send_otp(user, purpose='registration')
    if not sms_sent:
        return jsonify({'status': 'error', 'message': otp_message}), 500
    return jsonify({'status': 'success', 'message': otp_message})


# ── Login (email + password) ────────────────────────────────────────────
@auth_bp.route('/login', methods=['POST'])
@limiter.limit("15 per 10 minutes")
def login():
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not email or not password:
        return jsonify({'status': 'error', 'message': 'Email and password required'}), 400

    user = _user_by_email(email)
    password_ok = False
    if user:
        try:
            # Exact match first; then tolerate a stray leading/trailing space
            # added by phone keyboards / autofill.
            password_ok = user.check_password(password) or (
                password.strip() != password and user.check_password(password.strip())
            )
        except ValueError:
            password_ok = False
    if not user or not password_ok:
        return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 400
    if not user.is_active:
        return jsonify({'status': 'error', 'message': 'Account is blocked. Contact support.'}), 403
    if not user.is_verified:
        if can_resend_otp(user.id, purpose='registration'):
            _, _, otp_message = _send_otp(user, purpose='registration')
        else:
            # A code went out moments ago — don't fire another SMS (cost, and
            # confusion over which one to use). The OTP screen has Resend.
            otp_message = "An OTP was just sent. Check your SMS, or tap Resend OTP in a minute."
        return jsonify({
            'status': 'error', 'message': f'Account not verified. {otp_message}',
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


# ── Login with PIN (server-side — NEW) ──────────────────────────────────
# This is the endpoint that makes the Login PIN actually belong to the
# account rather than the device: it takes just a phone/email + PIN (no
# password, no existing session) and, on success, issues a brand new JWT —
# exactly like /login does. This is what lets a reinstalled app or a second
# phone use the same PIN instead of forcing the user through "Create PIN"
# again.
@auth_bp.route('/login-with-pin', methods=['POST'])
@limiter.limit("10 per 10 minutes")
def login_with_pin():
    data       = request.get_json() or {}
    identifier = data.get('identifier') or data.get('phone') or data.get('email')
    pin        = (data.get('pin') or '').strip()

    if not identifier or not pin:
        return jsonify({'status': 'error', 'message': 'Phone/email and PIN are required'}), 400

    user = _find_user_by_identifier(identifier)

    if not user:
        # Do a dummy bcrypt compare so a nonexistent account doesn't respond
        # measurably faster than a wrong-PIN response (avoids leaking which
        # phone numbers/emails have accounts via timing).
        bcrypt.checkpw(pin.encode('utf-8'), _DUMMY_HASH.encode('utf-8'))
        return jsonify({'status': 'error', 'message': 'Incorrect phone/email or PIN'}), 400

    if not user.is_active:
        return jsonify({'status': 'error', 'message': 'Account is blocked. Contact support.'}), 403

    result = user.check_login_pin(pin)
    db.session.commit()

    if result == 'locked':
        return jsonify({
            'status': 'error',
            'message': _lock_message(user.login_pin_lock_remaining()),
        }), 429
    if result == 'not_set':
        return jsonify({
            'status': 'error',
            'message': "PIN login isn't set up for this account yet. Please log in with your password.",
        }), 400
    if result != 'ok':
        return jsonify({'status': 'error', 'message': 'Incorrect phone/email or PIN'}), 400

    if not user.is_verified:
        return jsonify({
            'status': 'error', 'message': 'Account not verified. Please log in with your password.',
        }), 403

    gamification_service.record_daily_login(user)
    user.last_login = datetime.utcnow()
    db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify({
        'status':  'success',
        'message': 'Login successful',
        'data':    {'user': user.to_dict(), 'session_token': token},
    })


# ── Set / change Login PIN ───────────────────────────────────────────────
@auth_bp.route('/set-login-pin', methods=['POST'])
@jwt_required()
@limiter.limit("10 per hour")
def set_login_pin():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data    = request.get_json() or {}
    old_pin = data.get('old_pin')
    new_pin = data.get('new_pin')

    if user.login_pin_hash:
        # A PIN already exists — changing it requires proving the old one
        # (unless the caller is going through the OTP-verified /reset-pin
        # flow instead, which doesn't touch this endpoint).
        if not old_pin:
            return jsonify({'status': 'error', 'message': 'Current PIN required to change it'}), 400
        result = user.check_login_pin(old_pin)
        db.session.commit()
        if result == 'locked':
            return jsonify({'status': 'error', 'message': _lock_message(user.login_pin_lock_remaining())}), 429
        if result != 'ok':
            return jsonify({'status': 'error', 'message': 'Incorrect current PIN'}), 400

    if not new_pin or not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
        return jsonify({'status': 'error', 'message': 'PIN must be 4-6 digits'}), 400

    user.set_login_pin(new_pin)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Login PIN set successfully'})


# ── Set / change Transaction PIN ─────────────────────────────────────────
@auth_bp.route('/set-pin', methods=['POST'])
@jwt_required()
@limiter.limit("10 per hour")
def set_transaction_pin():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data    = request.get_json() or {}
    old_pin = data.get('old_pin')
    new_pin = data.get('new_pin')

    if user.transaction_pin_hash:
        if not old_pin:
            return jsonify({'status': 'error', 'message': 'Current PIN required to change it'}), 400
        result = user.check_transaction_pin(old_pin)
        db.session.commit()
        if result == 'locked':
            return jsonify({'status': 'error', 'message': _lock_message(user.transaction_pin_lock_remaining())}), 429
        if result != 'ok':
            return jsonify({'status': 'error', 'message': 'Incorrect current PIN'}), 400

    if not new_pin or not new_pin.isdigit() or not (4 <= len(new_pin) <= 6):
        return jsonify({'status': 'error', 'message': 'PIN must be 4-6 digits'}), 400

    user.set_transaction_pin(new_pin)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'PIN set successfully'})


# ── Verify Transaction PIN (standalone check, e.g. before showing a confirm screen) ──
@auth_bp.route('/verify-pin', methods=['POST'])
@jwt_required()
@limiter.limit("20 per 10 minutes")
def verify_pin():
    user_id = int(get_jwt_identity())
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = request.get_json() or {}
    pin  = data.get('pin')
    if not pin:
        return jsonify({'status': 'error', 'message': 'PIN required'}), 400

    result = user.check_transaction_pin(pin)
    db.session.commit()

    if result == 'not_set':
        return jsonify({'status': 'error', 'message': 'No PIN set. Please set a PIN first.'}), 400
    if result == 'locked':
        return jsonify({'status': 'error', 'message': _lock_message(user.transaction_pin_lock_remaining())}), 429
    if result == 'ok':
        return jsonify({'status': 'success', 'message': 'PIN verified'})
    return jsonify({'status': 'error', 'message': 'Incorrect PIN'}), 400


# ── Forgot PIN (login or transaction) ────────────────────────────────────
@auth_bp.route('/forgot-pin', methods=['POST'])
@limiter.limit("5 per 10 minutes", key_func=_body_key('email', 'phone'))
def forgot_pin():
    data     = request.get_json() or {}
    email    = (data.get('email') or '').strip().lower()
    phone    = (data.get('phone') or '').strip()
    pin_type = (data.get('pin_type') or '').strip().lower()

    if pin_type not in ('login', 'transaction'):
        return jsonify({'status': 'error', 'message': "pin_type must be 'login' or 'transaction'"}), 400

    user = None
    if email:
        user = _user_by_email(email)
    elif phone:
        user = User.query.filter_by(phone=phone).first()

    generic_ok = jsonify({'status': 'success', 'message': 'If this account exists, an OTP has been sent.'})
    if not user:
        return generic_ok

    purpose = f'{pin_type}_pin_reset'
    if not can_resend_otp(user.id, purpose=purpose):
        return jsonify({'status': 'error', 'message': f'Please wait {OTP_RESEND_COOLDOWN_SECONDS} seconds before requesting another OTP.'}), 429

    sms_sent, _, otp_message = _send_otp(user, purpose=purpose)
    if not sms_sent:
        return jsonify({'status': 'error', 'message': otp_message}), 500
    return jsonify({
        'status':  'success',
        'message': otp_message,
        'data':    {'user_id': user.id, 'phone': user.phone},
    })


# ── Reset PIN (login or transaction) — requires a verified OTP ──────────
@auth_bp.route('/reset-pin', methods=['POST'])
@limiter.limit("10 per 10 minutes", key_func=_body_key('user_id'))
def reset_pin():
    data     = request.get_json() or {}
    user_id  = data.get('user_id')
    otp_code = _clean_code(data.get('otp_code'))
    pin_type = (data.get('pin_type') or '').strip().lower()
    new_pin  = data.get('new_pin')

    if not all([user_id, otp_code, pin_type, new_pin]):
        return jsonify({'status': 'error', 'message': 'user_id, otp_code, pin_type and new_pin required'}), 400
    if pin_type not in ('login', 'transaction'):
        return jsonify({'status': 'error', 'message': "pin_type must be 'login' or 'transaction'"}), 400
    if not str(new_pin).isdigit() or not (4 <= len(str(new_pin)) <= 6):
        return jsonify({'status': 'error', 'message': 'PIN must be 4-6 digits'}), 400

    purpose = f'{pin_type}_pin_reset'
    otp = (
        OTP.query
        .filter_by(user_id=user_id, code=otp_code, is_used=False, purpose=purpose)
        .with_for_update()
        .first()
    )
    if not otp:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'OTP expired. Request a new one.'}), 400

    user = User.query.get(user_id)
    if not user:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    if pin_type == 'login':
        user.set_login_pin(str(new_pin))
    else:
        user.set_transaction_pin(str(new_pin))

    otp.is_used = True
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'{pin_type.capitalize()} PIN reset successfully'})


# ── Forgot Password ───────────────────────────────────────────────────
@auth_bp.route('/forgot-password', methods=['POST'])
@limiter.limit("5 per 10 minutes", key_func=_body_key('email', 'phone'))
def forgot_password():
    data  = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    phone = (data.get('phone') or '').strip()
    user  = None
    if email:
        user = _user_by_email(email)
    elif phone:
        user = User.query.filter_by(phone=phone).first()
    if not user:
        return jsonify({'status': 'success', 'message': 'If this account exists, an OTP has been sent.'})
    if not can_resend_otp(user.id, purpose='password_reset'):
        return jsonify({'status': 'error', 'message': 'Wait 60 seconds before requesting another OTP.'}), 429
    sms_sent, _, otp_message = _send_otp(user, purpose='password_reset')
    if not sms_sent:
        return jsonify({'status': 'error', 'message': otp_message}), 500
    return jsonify({
        'status':  'success',
        'message': otp_message,
        'data':    {'user_id': user.id, 'phone': user.phone},
    })


# ── Reset Password ────────────────────────────────────────────────────
@auth_bp.route('/reset-password', methods=['POST'])
@limiter.limit("10 per 10 minutes", key_func=_body_key('user_id'))
def reset_password():
    data         = request.get_json() or {}
    user_id      = data.get('user_id')
    otp_code     = _clean_code(data.get('otp_code'))
    new_password = data.get('new_password') or ''
    if not all([user_id, otp_code, new_password]):
        return jsonify({'status': 'error', 'message': 'user_id, otp_code and new_password required'}), 400
    if len(new_password) < 6:
        return jsonify({'status': 'error', 'message': 'Password must be at least 6 characters'}), 400
    otp = (
        OTP.query
        .filter_by(user_id=user_id, code=otp_code, is_used=False, purpose='password_reset')
        .with_for_update()
        .first()
    )
    if not otp:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'Invalid OTP code'}), 400
    if datetime.utcnow() > otp.expires_at:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'OTP expired. Request a new one.'}), 400
    user = User.query.get(user_id)
    if not user:
        db.session.rollback()
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
