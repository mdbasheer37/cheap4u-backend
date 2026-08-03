# cashback_routes.py — Cashback System API
#
# Two blueprints:
#   cashback_bp        /api/cashback/...        (any logged-in user)
#   cashback_admin_bp   /api/admin/cashback/...  (admin only)
#
# Registered in app.py alongside the other blueprints.

import csv
import io
import logging
from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required   # re-use the existing admin gate
from extensions import limiter
import cashback as cashback_service

logger = logging.getLogger(__name__)

cashback_bp = Blueprint('cashback', __name__, url_prefix='/api/cashback')
cashback_admin_bp = Blueprint('cashback_admin', __name__, url_prefix='/api/admin/cashback')


def _current_user_id():
    return int(get_jwt_identity())


# ── USER-FACING ──────────────────────────────────────────────────────────────

@cashback_bp.route('/wallet', methods=['GET'])
@jwt_required()
def wallet():
    user_id = _current_user_id()
    if not User.query.get(user_id):
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = cashback_service.get_wallet_summary(user_id)
    db.session.commit()  # persist any lazily-created wallet/config row
    return jsonify({'status': 'success', 'data': data})


@cashback_bp.route('/rates', methods=['GET'])
@jwt_required()
def rates():
    data = cashback_service.get_rates()
    db.session.commit()
    return jsonify({'status': 'success', 'data': data})


@cashback_bp.route('/history', methods=['GET'])
@jwt_required()
def history():
    user_id = _current_user_id()
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = cashback_service.get_history(user_id, page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'entries': rows, 'total': total, 'pages': pages, 'page': page},
    })


@cashback_bp.route('/redeem', methods=['POST'])
@jwt_required()
@limiter.limit('10 per hour')
def redeem():
    user_id = _current_user_id()
    if not User.query.get(user_id):
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    data = request.get_json(silent=True) or {}
    amount = data.get('amount')  # None means "redeem full balance"
    result = cashback_service.redeem(user_id, amount=amount)
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@cashback_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = cashback_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@cashback_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = cashback_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])

    float_fields = (
        'percent_airtime', 'percent_data', 'percent_electricity',
        'percent_cable_tv', 'percent_exam_pin', 'min_transaction_amount',
        'min_redeem_amount',
    )
    for field in float_fields:
        if field in data:
            try:
                setattr(cfg, field, float(data[field]))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400

    if 'max_cashback_per_transaction' in data:
        val = data['max_cashback_per_transaction']
        if val in (None, '', 0, '0'):
            cfg.max_cashback_per_transaction = None
        else:
            try:
                cfg.max_cashback_per_transaction = float(val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'Invalid value for max_cashback_per_transaction'}), 400

    if 'expiry_days' in data:
        val = data['expiry_days']
        if val in (None, '', 0, '0'):
            cfg.expiry_days = None
        else:
            try:
                cfg.expiry_days = int(val)
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': 'Invalid value for expiry_days'}), 400

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Cashback settings updated', 'data': cfg.to_dict()})


@cashback_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': cashback_service.get_platform_stats()})


@cashback_admin_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    search = request.args.get('search', '').strip() or None
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = cashback_service.list_wallets(search=search, page=page, per_page=per_page)
    return jsonify({
        'status': 'success',
        'data': {'wallets': rows, 'total': total, 'pages': pages, 'page': page},
    })


@cashback_admin_bp.route('/users/<int:user_id>/history', methods=['GET'])
@admin_required
def user_history(user_id):
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = cashback_service.get_history(user_id, page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'entries': rows, 'total': total, 'pages': pages, 'page': page},
    })


@cashback_admin_bp.route('/adjust/<int:user_id>', methods=['POST'])
@admin_required
def adjust(user_id):
    data = request.get_json() or {}
    if 'amount' not in data:
        return jsonify({'status': 'error', 'message': 'amount is required'}), 400
    result = cashback_service.admin_adjust(user_id, data.get('amount'), note=data.get('note'))
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@cashback_admin_bp.route('/expire-now', methods=['POST'])
@admin_required
def expire_now():
    """Manually trigger the expiry sweep — useful for testing or if the
    scheduler missed a run. Safe to call repeatedly (idempotent)."""
    result = cashback_service.expire_due_entries()
    return jsonify(result)


@cashback_admin_bp.route('/export', methods=['GET'])
@admin_required
def export_ledger():
    from cashback_models import CashbackEntry
    entries = (
        CashbackEntry.query
        .order_by(CashbackEntry.created_at.desc())
        .limit(20000)
        .all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Date', 'User ID', 'Type', 'Category', 'Amount', 'Balance After',
                      'Source Amount', 'Percent Applied', 'Expires At', 'Expired', 'Note'])
    for e in entries:
        writer.writerow([
            e.created_at.isoformat() if e.created_at else '',
            e.user_id, e.type, e.category or '',
            f'{e.amount:,.2f}', f'{e.balance_after:,.2f}',
            f'{e.source_amount:,.2f}' if e.source_amount is not None else '',
            e.percent_applied or '',
            e.expires_at.isoformat() if e.expires_at else '',
            'Yes' if e.is_expired else 'No',
            e.note or '',
        ])

    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=cashback_ledger.csv'},
    )
