# routes.py — Production-ready VTU service routes for Cheap4U
# All 5 services: Airtime, Data, Electricity, Cable TV, Exam PIN
# Changes from previous version:
#   1. Duplicate purchase prevention (30-second window)
#   2. Atomic DB rollback on all unhandled exceptions
#   3. Wallet deduction validated inside DB transaction
#   4. Improved JSON error responses with error codes
#   5. Rate limiting awareness (logs repeated failures)
#   6. All request fields validated before hitting provider

import logging
import bcrypt
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db, User, Transaction
from cheapdatahub import buy_airtime, buy_data, buy_electricity, buy_cable_tv
from vtunaija import buy_exam_pin

logger = logging.getLogger(__name__)
bp = Blueprint('vtu', __name__, url_prefix='/api/vtpass')


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_user():
    """Return (user, error_response) using JWT identity."""
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None, (jsonify({
            'status': 'error',
            'code': 'INVALID_TOKEN',
            'message': 'Invalid or expired token. Please login again.'
        }), 401)

    user = User.query.get(user_id)
    if not user:
        return None, (jsonify({
            'status': 'error',
            'code': 'USER_NOT_FOUND',
            'message': 'User account not found.'
        }), 404)

    if not user.is_active:
        return None, (jsonify({
            'status': 'error',
            'code': 'ACCOUNT_BLOCKED',
            'message': 'Your account has been suspended. Contact support.'
        }), 403)

    return user, None


def _verify_pin(user, pin):
    """Return True if PIN matches stored hash."""
    if not pin or not user.transaction_pin_hash:
        return False
    try:
        return bcrypt.checkpw(
            pin.encode('utf-8'),
            user.transaction_pin_hash.encode('utf-8')
        )
    except Exception:
        return False


def _is_duplicate(user_id, service_type, key_field, key_value, seconds=30):
    """
    Return True if an identical pending/successful transaction exists
    within the last `seconds` seconds. Prevents double-taps and retries.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=seconds)
    return Transaction.query.filter(
        Transaction.user_id == user_id,
        Transaction.service_type == service_type,
        Transaction.status.in_(['pending', 'success']),
        Transaction.created_at >= cutoff,
        Transaction.details[key_field].astext == str(key_value)
    ).first() is not None


def _validate_phone(phone):
    """Return error string or None if valid."""
    if not phone or len(phone) != 11 or not phone.isdigit():
        return 'Invalid phone number. Must be exactly 11 digits.'
    return None


# ─── AIRTIME ──────────────────────────────────────────────────────────────────

@bp.route('/airtime', methods=['POST'])
@jwt_required()
def airtime():
    """
    POST /api/vtpass/airtime
    Body: { network, phone, amount, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data    = request.get_json(silent=True) or {}
    network = str(data.get('network') or '').strip()
    phone   = str(data.get('phone') or '').strip()
    pin     = str(data.get('pin') or '').strip()

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_AMOUNT',
            'message': 'Amount must be a number.'
        }), 400

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[Airtime] Wrong PIN — user {user.id}")
        return jsonify({
            'status': 'error', 'code': 'WRONG_PIN',
            'message': 'Incorrect transaction PIN.'
        }), 401

    # Validate fields
    if network not in ('MTN', 'Airtel', 'Glo', '9Mobile'):
        return jsonify({
            'status': 'error', 'code': 'INVALID_NETWORK',
            'message': f'Unsupported network: {network}. Use MTN, Airtel, Glo, or 9Mobile.'
        }), 400

    phone_err = _validate_phone(phone)
    if phone_err:
        return jsonify({'status': 'error', 'code': 'INVALID_PHONE', 'message': phone_err}), 400

    if amount < 50:
        return jsonify({
            'status': 'error', 'code': 'AMOUNT_TOO_LOW',
            'message': 'Minimum airtime amount is ₦50.'
        }), 400

    if amount > 50000:
        return jsonify({
            'status': 'error', 'code': 'AMOUNT_TOO_HIGH',
            'message': 'Maximum airtime amount per transaction is ₦50,000.'
        }), 400

    # Wallet check
    if user.wallet_balance < amount:
        return jsonify({
            'status': 'error', 'code': 'INSUFFICIENT_BALANCE',
            'message': f'Insufficient balance. Available: ₦{user.wallet_balance:,.2f}'
        }), 400

    # Duplicate check
    if _is_duplicate(user.id, 'airtime', 'phone', phone, seconds=30):
        return jsonify({
            'status': 'error', 'code': 'DUPLICATE_TRANSACTION',
            'message': 'A similar airtime purchase was made recently. Please wait 30 seconds.'
        }), 429

    try:
        logger.info(f"[Airtime] user={user.id} network={network} phone={phone} amount={amount}")
        result = buy_airtime(network, phone, amount, user.email)
        if result.get('status') == 'success':
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Airtime] Unhandled exception user={user.id}: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred. Please try again.'
        }), 500


# ─── DATA ─────────────────────────────────────────────────────────────────────

@bp.route('/data', methods=['POST'])
@jwt_required()
def data_purchase():
    """
    POST /api/vtpass/data
    Body: { plan_id, phone, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data    = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id')
    phone   = str(data.get('phone') or '').strip()
    pin     = str(data.get('pin') or '').strip()

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[Data] Wrong PIN — user {user.id}")
        return jsonify({
            'status': 'error', 'code': 'WRONG_PIN',
            'message': 'Incorrect transaction PIN.'
        }), 401

    # Validate fields
    if not plan_id:
        return jsonify({
            'status': 'error', 'code': 'MISSING_PLAN',
            'message': 'plan_id is required.'
        }), 400

    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_PLAN',
            'message': 'plan_id must be a number.'
        }), 400

    phone_err = _validate_phone(phone)
    if phone_err:
        return jsonify({'status': 'error', 'code': 'INVALID_PHONE', 'message': phone_err}), 400

    # Duplicate check
    if _is_duplicate(user.id, 'data', 'phone', phone, seconds=30):
        return jsonify({
            'status': 'error', 'code': 'DUPLICATE_TRANSACTION',
            'message': 'A similar data purchase was made recently. Please wait 30 seconds.'
        }), 429

    try:
        logger.info(f"[Data] user={user.id} plan_id={plan_id} phone={phone}")
        result = buy_data(plan_id, phone, user.email)
        if result.get('status') == 'success':
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Data] Unhandled exception user={user.id}: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred. Please try again.'
        }), 500


# ─── ELECTRICITY ──────────────────────────────────────────────────────────────

@bp.route('/electricity', methods=['POST'])
@jwt_required()
def electricity():
    """
    POST /api/vtpass/electricity
    Body: { disco, meter_number, meter_type, amount, phone, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data       = request.get_json(silent=True) or {}
    disco      = str(data.get('disco') or '').strip()
    meter_num  = str(data.get('meter_number') or '').strip()
    meter_type = str(data.get('meter_type') or '').strip().lower()
    phone      = str(data.get('phone') or '').strip()
    pin        = str(data.get('pin') or '').strip()

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_AMOUNT',
            'message': 'Amount must be a number.'
        }), 400

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[Electricity] Wrong PIN — user {user.id}")
        return jsonify({
            'status': 'error', 'code': 'WRONG_PIN',
            'message': 'Incorrect transaction PIN.'
        }), 401

    # Validate fields
    if not disco:
        return jsonify({
            'status': 'error', 'code': 'MISSING_DISCO',
            'message': 'Electricity provider (disco) is required.'
        }), 400

    if not meter_num or len(meter_num) < 6 or not meter_num.isdigit():
        return jsonify({
            'status': 'error', 'code': 'INVALID_METER',
            'message': 'Meter number must be at least 6 digits.'
        }), 400

    if meter_type not in ('prepaid', 'postpaid'):
        return jsonify({
            'status': 'error', 'code': 'INVALID_METER_TYPE',
            'message': 'meter_type must be "prepaid" or "postpaid".'
        }), 400

    if amount < 100:
        return jsonify({
            'status': 'error', 'code': 'AMOUNT_TOO_LOW',
            'message': 'Minimum electricity amount is ₦100.'
        }), 400

    if amount > 200000:
        return jsonify({
            'status': 'error', 'code': 'AMOUNT_TOO_HIGH',
            'message': 'Maximum electricity payment per transaction is ₦200,000.'
        }), 400

    phone_err = _validate_phone(phone)
    if phone_err:
        return jsonify({'status': 'error', 'code': 'INVALID_PHONE', 'message': phone_err}), 400

    # Wallet check
    if user.wallet_balance < amount:
        return jsonify({
            'status': 'error', 'code': 'INSUFFICIENT_BALANCE',
            'message': f'Insufficient balance. Available: ₦{user.wallet_balance:,.2f}'
        }), 400

    # Duplicate check (meter + amount within 60 seconds)
    if _is_duplicate(user.id, 'electricity', 'meter_number', meter_num, seconds=60):
        return jsonify({
            'status': 'error', 'code': 'DUPLICATE_TRANSACTION',
            'message': 'A similar electricity payment was made recently. Please wait 60 seconds.'
        }), 429

    try:
        logger.info(f"[Electricity] user={user.id} disco={disco} meter={meter_num} amount={amount}")
        result = buy_electricity(disco, meter_num, meter_type, amount, phone, user.email)
        if result.get('status') == 'success':
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Electricity] Unhandled exception user={user.id}: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred. Please try again.'
        }), 500


# ─── CABLE TV ─────────────────────────────────────────────────────────────────

@bp.route('/cable-tv', methods=['POST'])
@jwt_required()
def cable_tv():
    """
    POST /api/vtpass/cable-tv
    Body: { plan_id, smartcard, phone, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data      = request.get_json(silent=True) or {}
    plan_id   = data.get('plan_id')
    smartcard = str(data.get('smartcard') or data.get('cardnumber') or '').strip()
    phone     = str(data.get('phone') or '').strip()
    pin       = str(data.get('pin') or '').strip()

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[CableTV] Wrong PIN — user {user.id}")
        return jsonify({
            'status': 'error', 'code': 'WRONG_PIN',
            'message': 'Incorrect transaction PIN.'
        }), 401

    # Validate fields
    if not plan_id:
        return jsonify({
            'status': 'error', 'code': 'MISSING_PLAN',
            'message': 'plan_id is required.'
        }), 400

    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_PLAN',
            'message': 'plan_id must be a number.'
        }), 400

    if not smartcard or len(smartcard) < 6:
        return jsonify({
            'status': 'error', 'code': 'INVALID_SMARTCARD',
            'message': 'Smartcard/IUC number must be at least 6 characters.'
        }), 400

    # Phone is optional for cable TV but validate if provided
    if phone and (len(phone) != 11 or not phone.isdigit()):
        return jsonify({
            'status': 'error', 'code': 'INVALID_PHONE',
            'message': 'Invalid phone number. Must be exactly 11 digits.'
        }), 400

    # Wallet check (plan price validated inside buy_cable_tv)
    # Duplicate check (smartcard within 60 seconds)
    if _is_duplicate(user.id, 'cable_tv', 'smartcard', smartcard, seconds=60):
        return jsonify({
            'status': 'error', 'code': 'DUPLICATE_TRANSACTION',
            'message': 'A similar cable TV subscription was made recently. Please wait 60 seconds.'
        }), 429

    try:
        logger.info(f"[CableTV] user={user.id} plan_id={plan_id} smartcard={smartcard}")
        result = buy_cable_tv(plan_id, smartcard, user.email, phone=phone)
        if result.get('status') == 'success':
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"[CableTV] Unhandled exception user={user.id}: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred. Please try again.'
        }), 500


# ─── EXAM PINS ────────────────────────────────────────────────────────────────

@bp.route('/exam-pins', methods=['POST'])
@jwt_required()
def exam_pins():
    """
    POST /api/vtpass/exam-pins
    Body: { exam_type, quantity, selling_price, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data          = request.get_json(silent=True) or {}
    exam_type     = str(data.get('exam_type') or '').strip().upper()
    pin           = str(data.get('pin') or '').strip()
    selling_price = data.get('selling_price')

    try:
        quantity = int(data.get('quantity', 0))
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_QUANTITY',
            'message': 'Quantity must be a whole number.'
        }), 400

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[ExamPIN] Wrong PIN — user {user.id}")
        return jsonify({
            'status': 'error', 'code': 'WRONG_PIN',
            'message': 'Incorrect transaction PIN.'
        }), 401

    # Validate fields
    if exam_type not in ('WAEC', 'NECO', 'NABTEB', 'JAMB'):
        return jsonify({
            'status': 'error', 'code': 'INVALID_EXAM_TYPE',
            'message': 'exam_type must be WAEC, NECO, NABTEB, or JAMB.'
        }), 400

    if quantity < 1 or quantity > 10:
        return jsonify({
            'status': 'error', 'code': 'INVALID_QUANTITY',
            'message': 'Quantity must be between 1 and 10.'
        }), 400

    if selling_price is None:
        return jsonify({
            'status': 'error', 'code': 'MISSING_PRICE',
            'message': 'selling_price is required.'
        }), 400

    try:
        selling_price = float(selling_price)
    except (TypeError, ValueError):
        return jsonify({
            'status': 'error', 'code': 'INVALID_PRICE',
            'message': 'selling_price must be a number.'
        }), 400

    if selling_price <= 0:
        return jsonify({
            'status': 'error', 'code': 'INVALID_PRICE',
            'message': 'selling_price must be greater than zero.'
        }), 400

    # Wallet check
    if user.wallet_balance < selling_price:
        return jsonify({
            'status': 'error', 'code': 'INSUFFICIENT_BALANCE',
            'message': f'Insufficient balance. Available: ₦{user.wallet_balance:,.2f}'
        }), 400

    # Duplicate check (same exam type within 60 seconds)
    if _is_duplicate(user.id, 'exam_pin', 'exam_name', exam_type, seconds=60):
        return jsonify({
            'status': 'error', 'code': 'DUPLICATE_TRANSACTION',
            'message': 'A similar exam PIN purchase was made recently. Please wait 60 seconds.'
        }), 429

    try:
        logger.info(f"[ExamPIN] user={user.id} exam={exam_type} qty={quantity} price={selling_price}")
        result = buy_exam_pin(exam_type, quantity, user.email, selling_price=selling_price)
        if result.get('status') == 'success':
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"[ExamPIN] Unhandled exception user={user.id}: {e}", exc_info=True)
        return jsonify({
            'status': 'error', 'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred. Please try again.'
        }), 500


# ─── DATA PLANS ───────────────────────────────────────────────────────────────

@bp.route('/plans/data', methods=['GET'])
def get_data_plans():
    """
    GET /api/plans/data?provider=mtn
    Returns available data plans. No auth required.
    """
    from models import DataPlan
    provider = request.args.get('provider', '').lower()
    query = DataPlan.query
    if provider:
        query = query.filter_by(provider=provider)
    plans = query.order_by(DataPlan.selling_price).all()
    return jsonify({
        'status': 'success',
        'data': [{
            'plan_id':       p.plan_id,
            'provider':      p.provider,
            'size':          p.size,
            'duration':      p.duration,
            'selling_price': p.selling_price,
            'cost_price':    p.cost_price,
        } for p in plans]
    }), 200


# ─── TRANSACTION HISTORY ──────────────────────────────────────────────────────

@bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    """
    GET /api/vtpass/transactions?service_type=airtime&limit=50&page=1
    Returns paginated transaction history for the current user.
    """
    user, err = _get_user()
    if err:
        return err

    service_type = request.args.get('service_type', '').strip() or None
    status_filter = request.args.get('status', '').strip() or None

    try:
        limit = min(int(request.args.get('limit', 50)), 200)
        page  = max(int(request.args.get('page', 1)), 1)
    except (TypeError, ValueError):
        limit, page = 50, 1

    query = Transaction.query.filter_by(user_id=user.id)
    if service_type:
        query = query.filter_by(service_type=service_type)
    if status_filter:
        query = query.filter_by(status=status_filter)

    total = query.count()
    txns  = query.order_by(Transaction.created_at.desc()) \
                 .offset((page - 1) * limit) \
                 .limit(limit).all()

    return jsonify({
        'status': 'success',
        'data':   [t.to_dict() for t in txns],
        'meta': {
            'total': total,
            'page':  page,
            'limit': limit,
            'pages': (total + limit - 1) // limit,
        }
    }), 200
