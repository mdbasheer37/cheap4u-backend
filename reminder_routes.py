# reminder_routes.py — Bill Reminder API
#
# Two blueprints:
#   reminder_bp        /api/reminders/...        (any logged-in user)
#   reminder_admin_bp   /api/admin/reminders/...  (admin only)

import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db
from admin import admin_required
import reminder as reminder_service

logger = logging.getLogger(__name__)

reminder_bp = Blueprint('reminder', __name__, url_prefix='/api/reminders')
reminder_admin_bp = Blueprint('reminder_admin', __name__, url_prefix='/api/admin/reminders')


def _current_user_id():
    return int(get_jwt_identity())


# ── USER-FACING ──────────────────────────────────────────────────────────────

@reminder_bp.route('', methods=['GET'])
@jwt_required()
def list_reminders():
    active_only = request.args.get('active_only', 'false').lower() == 'true'
    rows = reminder_service.list_reminders(_current_user_id(), active_only=active_only)
    return jsonify({'status': 'success', 'data': [r.to_dict() for r in rows]})


@reminder_bp.route('', methods=['POST'])
@jwt_required()
def create_reminder():
    data = request.get_json() or {}
    reminder, error = reminder_service.create_reminder(_current_user_id(), data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Reminder created', 'data': reminder.to_dict()}), 201


@reminder_bp.route('/<int:reminder_id>', methods=['PUT'])
@jwt_required()
def update_reminder(reminder_id):
    data = request.get_json() or {}
    reminder, error = reminder_service.update_reminder(_current_user_id(), reminder_id, data)
    if error:
        return jsonify({'status': 'error', 'message': error}), 400
    return jsonify({'status': 'success', 'message': 'Reminder updated', 'data': reminder.to_dict()})


@reminder_bp.route('/<int:reminder_id>', methods=['DELETE'])
@jwt_required()
def delete_reminder(reminder_id):
    ok, error = reminder_service.delete_reminder(_current_user_id(), reminder_id)
    if not ok:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Reminder deleted'})


@reminder_bp.route('/pending', methods=['GET'])
@jwt_required()
def pending_push():
    """Polled by the app on open to display local (plyer) notifications
    for anything queued on the push channel."""
    logs = reminder_service.get_pending_push(_current_user_id())
    return jsonify({'status': 'success', 'data': [l.to_dict() for l in logs]})


@reminder_bp.route('/pending/<int:log_id>/ack', methods=['POST'])
@jwt_required()
def ack_pending(log_id):
    ok, error = reminder_service.ack_push(_current_user_id(), log_id)
    if not ok:
        return jsonify({'status': 'error', 'message': error}), 404
    return jsonify({'status': 'success', 'message': 'Acknowledged'})


@reminder_bp.route('/history', methods=['GET'])
@jwt_required()
def history():
    reminder_id = request.args.get('reminder_id', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    rows, total, pages = reminder_service.get_history(_current_user_id(), reminder_id, page, per_page)
    return jsonify({'status': 'success', 'data': {'logs': rows, 'total': total, 'pages': pages, 'page': page}})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@reminder_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = reminder_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@reminder_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = reminder_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    if 'default_reminder_days_before' in data:
        try:
            cfg.default_reminder_days_before = max(0, int(data['default_reminder_days_before']))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid default_reminder_days_before'}), 400
    if 'default_channels' in data:
        channels = data['default_channels']
        if isinstance(channels, list):
            cfg.default_channels = ','.join(channels)
        else:
            cfg.default_channels = channels
    if 'sms_provider_configured' in data:
        cfg.sms_provider_configured = bool(data['sms_provider_configured'])
    if 'email_provider_configured' in data:
        cfg.email_provider_configured = bool(data['email_provider_configured'])

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Reminder settings updated', 'data': cfg.to_dict()})


@reminder_admin_bp.route('/stats', methods=['GET'])
@admin_required
def stats():
    return jsonify({'status': 'success', 'data': reminder_service.get_platform_stats()})


@reminder_admin_bp.route('/run-now', methods=['POST'])
@admin_required
def run_now():
    """Manually trigger the dispatch + advance sweep — useful for testing
    without waiting for the scheduled interval."""
    dispatch_result = reminder_service.check_and_dispatch_due_reminders()
    advance_result = reminder_service.advance_past_due_reminders()
    return jsonify({'status': 'success', 'data': {'dispatch': dispatch_result, 'advance': advance_result}})
