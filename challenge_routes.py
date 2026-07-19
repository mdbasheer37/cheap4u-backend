# challenge_routes.py — Monthly Champion Challenge API
#
# Two blueprints:
#   challenge_bp        /api/challenge/...        (any logged-in user)
#   challenge_admin_bp   /api/admin/challenge/...  (admin only)
#
# Registered in app.py alongside the other blueprints.

import csv
import io
import logging
from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity

from models import db, User
from admin import admin_required   # re-use the existing admin gate
import challenge as challenge_service
from challenge_models import ChallengeConfig, ChallengeWinner, ChallengeEntry

logger = logging.getLogger(__name__)

challenge_bp = Blueprint('challenge', __name__, url_prefix='/api/challenge')
challenge_admin_bp = Blueprint('challenge_admin', __name__, url_prefix='/api/admin/challenge')


def _current_user():
    user_id = int(get_jwt_identity())
    return User.query.get(user_id)


# ── USER-FACING ──────────────────────────────────────────────────────────────

@challenge_bp.route('/leaderboard', methods=['GET'])
@jwt_required()
def leaderboard():
    month = request.args.get('month')  # optional 'YYYY-MM', defaults to current
    limit = min(request.args.get('limit', 100, type=int), 200)
    board, resolved_month = challenge_service.get_leaderboard(month=month, limit=limit)
    cfg = challenge_service.get_config()
    return jsonify({
        'status': 'success',
        'data': {
            'month':              resolved_month,
            'challenge_enabled':  cfg.is_enabled,
            'countdown_seconds':  challenge_service.seconds_until_month_end(),
            'total_participants': len(board),
            'leaderboard':        board,
            'rewards': {
                'first_place_percent': cfg.first_place_percent,
                'second_place_bonus':  cfg.second_place_bonus,
                'third_place_bonus':   cfg.third_place_bonus,
            },
        },
    })


@challenge_bp.route('/my-summary', methods=['GET'])
@jwt_required()
def my_summary():
    user = _current_user()
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    data = challenge_service.get_user_summary(user.id)
    return jsonify({'status': 'success', 'data': data})


@challenge_bp.route('/winners', methods=['GET'])
@jwt_required()
def winners_history():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 30, type=int), 100)
    winners, total, pages = challenge_service.get_winners_history(page, per_page)
    return jsonify({
        'status': 'success',
        'data': {'winners': winners, 'total': total, 'pages': pages, 'page': page},
    })


@challenge_bp.route('/notifications', methods=['GET'])
@jwt_required()
def notifications():
    user_id = int(get_jwt_identity())
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    rows = challenge_service.get_notifications(user_id, unread_only=unread_only)
    unread_count = sum(1 for r in rows if not r['is_read']) if not unread_only else len(rows)
    return jsonify({'status': 'success', 'data': {'notifications': rows, 'unread_count': unread_count}})


@challenge_bp.route('/notifications/read', methods=['POST'])
@jwt_required()
def mark_read():
    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    challenge_service.mark_notifications_read(user_id, notification_id=data.get('notification_id'))
    return jsonify({'status': 'success', 'message': 'Notifications marked as read'})


# ── ADMIN ─────────────────────────────────────────────────────────────────────

@challenge_admin_bp.route('/config', methods=['GET'])
@admin_required
def get_config():
    cfg = challenge_service.get_config()
    db.session.commit()
    return jsonify({'status': 'success', 'data': cfg.to_dict()})


@challenge_admin_bp.route('/config', methods=['POST'])
@admin_required
def update_config():
    data = request.get_json() or {}
    cfg = challenge_service.get_config()

    if 'is_enabled' in data:
        cfg.is_enabled = bool(data['is_enabled'])
    for field in ('first_place_percent', 'second_place_bonus', 'third_place_bonus', 'min_qualifying_amount'):
        if field in data:
            try:
                setattr(cfg, field, float(data[field]))
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': f'Invalid value for {field}'}), 400

    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Challenge settings updated', 'data': cfg.to_dict()})


@challenge_admin_bp.route('/leaderboard', methods=['GET'])
@admin_required
def admin_leaderboard():
    month = request.args.get('month')
    limit = min(request.args.get('limit', 500, type=int), 1000)
    board, resolved_month = challenge_service.get_leaderboard(month=month, limit=limit)
    return jsonify({'status': 'success', 'data': {'month': resolved_month, 'leaderboard': board}})


@challenge_admin_bp.route('/credit/<int:winner_id>', methods=['POST'])
@admin_required
def admin_credit_winner(winner_id):
    result = challenge_service.manual_credit_winner(winner_id)
    status_code = 200 if result.get('status') == 'success' else 400
    return jsonify(result), status_code


@challenge_admin_bp.route('/process-month', methods=['POST'])
@admin_required
def admin_process_month():
    """Manually trigger month-end archiving/crediting — useful for testing
    or if the scheduler missed a run. Idempotent per month unless force=true."""
    data = request.get_json(silent=True) or {}
    month = data.get('month')
    force = bool(data.get('force', False))
    result = challenge_service.process_month_end(month_key=month, force=force)
    return jsonify(result)


@challenge_admin_bp.route('/export/winners', methods=['GET'])
@admin_required
def export_winners():
    winners = ChallengeWinner.query.order_by(
        ChallengeWinner.month.desc(), ChallengeWinner.rank.asc()
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Month', 'Rank', 'User ID', 'Name', 'Total Purchases', 'Reward Amount', 'Reward Type', 'Credited', 'Credited At'])
    for w in winners:
        writer.writerow([
            w.month, w.rank, w.user_id, w.user_name,
            f"{w.total_amount:,.2f}", f"{w.reward_amount:,.2f}", w.reward_type,
            'Yes' if w.credited else 'No',
            w.credited_at.isoformat() if w.credited_at else '',
        ])

    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=challenge_winners.csv'},
    )


@challenge_admin_bp.route('/export/leaderboard', methods=['GET'])
@admin_required
def export_leaderboard():
    month = request.args.get('month')
    board, resolved_month = challenge_service.get_leaderboard(month=month, limit=1000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Rank', 'User ID', 'Name', 'Total Purchases', 'Purchase Count', 'Reward Position'])
    for row in board:
        writer.writerow([
            row['rank'], row['user_id'], row['name'],
            f"{row['total_amount']:,.2f}", row['purchase_count'], row['reward_position'] or '',
        ])

    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=leaderboard_{resolved_month}.csv'},
    )
