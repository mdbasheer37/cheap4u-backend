# routes.py — VTU service routes for Cheap4U
# This file was MISSING — its absence caused ALL VTU purchases to return 404.
# Providers: CheapDataHub (airtime/data/electricity/cable_tv) + VtuNaija (exam pins)
# Drop this file in the same folder as app.py, cheapdatahub.py, and vtunaija.py.

import logging
import bcrypt
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from models import db, User, Transaction
from cheapdatahub import buy_airtime, buy_data, buy_electricity, buy_cable_tv
from vtunaija import buy_exam_pin

logger = logging.getLogger(__name__)

bp = Blueprint('vtu', __name__, url_prefix='/api/vtpass')


# ─── helpers ──────────────────────────────────────────────────────────────────

def _get_user():
    """Return (user, error_response). Uses JWT identity."""
    try:
        user_id = int(get_jwt_identity())
    except (TypeError, ValueError):
        return None, (jsonify({'status': 'error', 'message': 'Invalid token'}), 401)
    user = User.query.get(user_id)
    if not user:
        return None, (jsonify({'status': 'error', 'message': 'User not found'}), 404)
    if not user.is_active:
        return None, (jsonify({'status': 'error', 'message': 'Account is blocked'}), 403)
    return user, None


def _verify_pin(user, pin):
    """Return True if the supplied PIN matches the user's stored PIN hash."""
    if not pin or not user.transaction_pin_hash:
        return False
    try:
        return bcrypt.checkpw(pin.encode('utf-8'), user.transaction_pin_hash.encode('utf-8'))
    except Exception:
        return False


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

    data    = request.get_json() or {}
    network = (data.get('network') or '').strip()
    phone   = (data.get('phone') or '').strip()
    pin     = (data.get('pin') or '').strip()

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    # Validate PIN
    if not _verify_pin(user, pin):
        logger.warning(f"[Airtime] Wrong PIN — user {user.id}")
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    # Basic field validation (detailed validation happens inside buy_airtime too)
    if network not in ('MTN', 'Airtel', 'Glo', '9Mobile'):
        return jsonify({'status': 'error', 'message': f'Unsupported network: {network}'}), 400
    if not phone or len(phone) != 11 or not phone.isdigit():
        return jsonify({'status': 'error', 'message': 'Invalid phone number (must be 11 digits)'}), 400
    if amount < 50:
        return jsonify({'status': 'error', 'message': 'Minimum airtime amount is ₦50'}), 400

    logger.info(f"[Airtime] user={user.id} network={network} phone={phone} amount={amount}")
    result = buy_airtime(network, phone, amount, user.email)

    if result.get('status') == 'success':
        return jsonify(result), 200
    return jsonify(result), 400


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

    data    = request.get_json() or {}
    plan_id = data.get('plan_id')
    phone   = (data.get('phone') or '').strip()
    pin     = (data.get('pin') or '').strip()

    if not _verify_pin(user, pin):
        logger.warning(f"[Data] Wrong PIN — user {user.id}")
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    if not plan_id:
        return jsonify({'status': 'error', 'message': 'plan_id is required'}), 400
    if not phone or len(phone) != 11 or not phone.isdigit():
        return jsonify({'status': 'error', 'message': 'Invalid phone number (must be 11 digits)'}), 400

    logger.info(f"[Data] user={user.id} plan_id={plan_id} phone={phone}")
    result = buy_data(plan_id, phone, user.email)

    if result.get('status') == 'success':
        return jsonify(result), 200
    return jsonify(result), 400


# ─── ELECTRICITY ───────────────────────────────────────────────────────────────

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

    data       = request.get_json() or {}
    disco      = (data.get('disco') or '').strip()
    meter_num  = (data.get('meter_number') or '').strip()
    meter_type = (data.get('meter_type') or '').strip()
    phone      = (data.get('phone') or '').strip()
    pin        = (data.get('pin') or '').strip()

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid amount'}), 400

    if not _verify_pin(user, pin):
        logger.warning(f"[Electricity] Wrong PIN — user {user.id}")
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    if not disco:
        return jsonify({'status': 'error', 'message': 'disco (electricity company) is required'}), 400
    if not meter_num or len(meter_num) < 6 or not meter_num.isdigit():
        return jsonify({'status': 'error', 'message': 'Invalid meter number (minimum 6 digits)'}), 400
    if meter_type not in ('prepaid', 'postpaid'):
        return jsonify({'status': 'error', 'message': 'meter_type must be prepaid or postpaid'}), 400
    if amount < 50:
        return jsonify({'status': 'error', 'message': 'Minimum electricity amount is ₦50'}), 400
    if not phone or len(phone) != 11 or not phone.isdigit():
        return jsonify({'status': 'error', 'message': 'Invalid phone number (must be 11 digits)'}), 400

    logger.info(f"[Electricity] user={user.id} disco={disco} meter={meter_num} amount={amount}")
    result = buy_electricity(disco, meter_num, meter_type, amount, phone, user.email)

    if result.get('status') == 'success':
        return jsonify(result), 200
    return jsonify(result), 400


# ─── CABLE TV ─────────────────────────────────────────────────────────────────

@bp.route('/cable-tv', methods=['POST'])
@jwt_required()
def cable_tv():
    """
    POST /api/vtpass/cable-tv
    Body: { plan_id, smartcard, pin, user_email }
    """
    user, err = _get_user()
    if err:
        return err

    data      = request.get_json() or {}
    plan_id   = data.get('plan_id')
    smartcard = (data.get('smartcard') or '').strip()
    pin       = (data.get('pin') or '').strip()

    if not _verify_pin(user, pin):
        logger.warning(f"[CableTV] Wrong PIN — user {user.id}")
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    if not plan_id:
        return jsonify({'status': 'error', 'message': 'plan_id is required'}), 400
    if not smartcard or len(smartcard) < 6:
        return jsonify({'status': 'error', 'message': 'Invalid smartcard/IUC number (minimum 6 characters)'}), 400

    logger.info(f"[CableTV] user={user.id} plan_id={plan_id} smartcard={smartcard}")
    result = buy_cable_tv(plan_id, smartcard, user.email)

    if result.get('status') == 'success':
        return jsonify(result), 200
    return jsonify(result), 400


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

    data          = request.get_json() or {}
    exam_type     = (data.get('exam_type') or '').strip().upper()
    pin           = (data.get('pin') or '').strip()
    selling_price = data.get('selling_price')

    try:
        quantity = int(data.get('quantity', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid quantity'}), 400

    if not _verify_pin(user, pin):
        logger.warning(f"[ExamPIN] Wrong PIN — user {user.id}")
        return jsonify({'status': 'error', 'message': 'Incorrect transaction PIN'}), 401

    if exam_type not in ('WAEC', 'NECO', 'NABTEB', 'JAMB'):
        return jsonify({'status': 'error',
                        'message': 'exam_type must be WAEC, NECO, NABTEB, or JAMB'}), 400
    if quantity < 1 or quantity > 10:
        return jsonify({'status': 'error', 'message': 'Quantity must be between 1 and 10'}), 400
    if selling_price is None:
        return jsonify({'status': 'error', 'message': 'selling_price is required'}), 400

    try:
        selling_price = float(selling_price)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'Invalid selling_price'}), 400

    logger.info(f"[ExamPIN] user={user.id} exam={exam_type} qty={quantity} price={selling_price}")
    result = buy_exam_pin(exam_type, quantity, user.email, selling_price=selling_price)

    if result.get('status') == 'success':
        return jsonify(result), 200
    return jsonify(result), 400


# ─── TRANSACTION HISTORY ──────────────────────────────────────────────────────

@bp.route('/transactions', methods=['GET'])
@jwt_required()
def get_transactions():
    """
    GET /api/vtpass/transactions?service_type=airtime&limit=50
    Returns transaction history for the current user.
    """
    user, err = _get_user()
    if err:
        return err

    service_type = request.args.get('service_type')
    try:
        limit = min(int(request.args.get('limit', 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    query = Transaction.query.filter_by(user_id=user.id)
    if service_type:
        query = query.filter_by(service_type=service_type)

    txns = query.order_by(Transaction.created_at.desc()).limit(limit).all()
    return jsonify({
        'status': 'success',
        'data': [t.to_dict() for t in txns]
    }), 200
