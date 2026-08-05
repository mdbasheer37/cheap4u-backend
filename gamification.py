# gamification.py — Gamification: core business logic (XP, Levels, Badges,
# Missions, Leaderboard)
#
# Self-contained, following the same shape as cashback.py / spin.py.
# record_activity(transaction) is the hook every purchase function calls
# — same non-blocking, never-break-a-real-purchase pattern as
# cashback.award_cashback(). record_daily_login(user) is the second hook,
# called once from auth.py's login route.

import logging
from datetime import datetime, date

from models import db, User, Transaction
from gamification_models import (
    GamificationConfig, XPLevel, UserXP, XPEntry,
    Badge, UserBadge, Mission, UserMissionProgress,
)

logger = logging.getLogger(__name__)

DEFAULT_LEVELS = [
    (1, 'Bronze',   0,     'star-outline'),
    (2, 'Silver',   200,   'star-half-full'),
    (3, 'Gold',     600,   'star'),
    (4, 'Platinum', 1500,  'star-face'),
    (5, 'Diamond',  4000,  'crown'),
]


def get_config():
    cfg = GamificationConfig.query.get(1)
    if not cfg:
        cfg = GamificationConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def _seed_default_levels_if_empty():
    if XPLevel.query.first():
        return
    for num, title, xp, icon in DEFAULT_LEVELS:
        db.session.add(XPLevel(level_number=num, title=title, xp_required=xp, icon=icon))
    db.session.flush()


def get_levels():
    _seed_default_levels_if_empty()
    return XPLevel.query.order_by(XPLevel.xp_required.asc()).all()


def _compute_level(total_xp):
    levels = get_levels()
    current = levels[0] if levels else None
    for lvl in levels:
        if total_xp >= lvl.xp_required:
            current = lvl
        else:
            break
    return current


def get_or_create_user_xp(user_id):
    row = UserXP.query.filter_by(user_id=user_id).first()
    if not row:
        row = UserXP(user_id=user_id, total_xp=0, current_level=1)
        db.session.add(row)
        db.session.flush()
    return row


# ── XP awarding ──────────────────────────────────────────────────────────────

def award_xp(user_id, amount, source, description=None):
    """Adds XP, recomputes level, and — on level-up — credits bonus points
    into the Spin & Win points wallet so it lands somewhere the user
    already sees. Never raises; caller decides whether to log failures."""
    if amount <= 0:
        return None

    xp_row = get_or_create_user_xp(user_id)
    old_level = xp_row.current_level or 1

    xp_row.total_xp = (xp_row.total_xp or 0) + int(round(amount))
    new_level_obj = _compute_level(xp_row.total_xp)
    new_level = new_level_obj.level_number if new_level_obj else old_level
    xp_row.current_level = new_level

    db.session.add(XPEntry(user_id=user_id, amount=int(round(amount)), source=source, description=description))

    leveled_up = new_level > old_level
    if leveled_up:
        cfg = get_config()
        if cfg.level_up_bonus_points > 0:
            try:
                from spin import _get_or_create_points
                points = _get_or_create_points(user_id)
                points.balance = (points.balance or 0) + cfg.level_up_bonus_points
            except Exception:
                logger.exception('[Gamification] failed to credit level-up bonus points (non-fatal)')

    return {'leveled_up': leveled_up, 'new_level': new_level, 'total_xp': xp_row.total_xp}


def record_daily_login(user):
    """Called from auth.py's login route, BEFORE user.last_login is
    overwritten with the new timestamp, so we can tell whether this is
    the first login of a new calendar day."""
    try:
        cfg = get_config()
        if not cfg.is_enabled or cfg.xp_daily_login <= 0:
            return
        already_today = user.last_login and user.last_login.date() == date.today()
        if already_today:
            return
        award_xp(user.id, cfg.xp_daily_login, 'daily_login', 'Daily login bonus')
    except Exception:
        logger.exception('[Gamification] record_daily_login failed (non-fatal)')


# ── missions ─────────────────────────────────────────────────────────────────

def _period_key(period):
    today = date.today()
    if period == 'daily':
        return today.isoformat()
    iso = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def get_active_missions(period=None):
    q = Mission.query.filter_by(is_active=True)
    if period:
        q = q.filter_by(period=period)
    return q.all()


def _get_or_create_progress(user_id, mission):
    key = _period_key(mission.period)
    row = UserMissionProgress.query.filter_by(user_id=user_id, mission_id=mission.id, period_key=key).first()
    if not row:
        row = UserMissionProgress(user_id=user_id, mission_id=mission.id, period_key=key,
                                   progress_value=0.0, is_completed=False)
        db.session.add(row)
        db.session.flush()
    return row


def _update_mission_progress(user_id, transaction):
    missions = get_active_missions()
    for mission in missions:
        if mission.category and mission.category != transaction.type:
            continue
        if mission.target_type not in ('transaction_count', 'spend_amount', 'category_count'):
            continue
        if mission.target_type == 'category_count' and not mission.category:
            continue  # category_count needs a category filter to mean anything

        progress = _get_or_create_progress(user_id, mission)
        if progress.is_completed:
            continue

        if mission.target_type == 'spend_amount':
            progress.progress_value = (progress.progress_value or 0) + transaction.amount
        else:  # transaction_count or category_count
            progress.progress_value = (progress.progress_value or 0) + 1

        if progress.progress_value >= mission.target_value:
            progress.is_completed = True
            progress.completed_at = datetime.utcnow()
            if mission.xp_reward:
                award_xp(user_id, mission.xp_reward, 'mission', f'Completed: {mission.title}')
            if mission.points_reward:
                try:
                    from spin import _get_or_create_points
                    points = _get_or_create_points(user_id)
                    points.balance = (points.balance or 0) + mission.points_reward
                except Exception:
                    logger.exception('[Gamification] failed to credit mission points (non-fatal)')


def get_user_missions(user_id):
    missions = get_active_missions()
    result = []
    for mission in missions:
        progress = _get_or_create_progress(user_id, mission)
        d = mission.to_dict()
        d['progress_value'] = progress.progress_value
        d['is_completed'] = progress.is_completed
        result.append(d)
    db.session.commit()
    return result


# ── badges ───────────────────────────────────────────────────────────────────

def _user_stats(user_id):
    total_transactions = Transaction.query.filter_by(user_id=user_id, status='success').count()
    total_spent = (
        db.session.query(db.func.coalesce(db.func.sum(Transaction.amount), 0.0))
        .filter(Transaction.user_id == user_id, Transaction.status == 'success').scalar() or 0.0
    )
    xp_row = get_or_create_user_xp(user_id)
    return {'total_transactions': total_transactions, 'total_spent': total_spent, 'total_xp': xp_row.total_xp or 0}


def _category_count(user_id, category):
    return Transaction.query.filter_by(user_id=user_id, status='success', type=category).count()


def _check_badges(user_id, transaction):
    badges = Badge.query.filter_by(is_active=True).all()
    if not badges:
        return
    already_earned_ids = {ub.badge_id for ub in UserBadge.query.filter_by(user_id=user_id).all()}
    stats = _user_stats(user_id)

    for badge in badges:
        if badge.id in already_earned_ids:
            continue

        if badge.criteria_type == 'total_transactions':
            value = stats['total_transactions']
        elif badge.criteria_type == 'total_spent':
            value = stats['total_spent']
        elif badge.criteria_type == 'total_xp':
            value = stats['total_xp']
        elif badge.criteria_type == 'category_transactions':
            if not badge.category:
                continue
            value = _category_count(user_id, badge.category)
        else:
            continue

        if value >= badge.criteria_value:
            db.session.add(UserBadge(user_id=user_id, badge_id=badge.id))
            award_xp(user_id, 15, 'badge', f'Earned badge: {badge.name}')


def get_user_badges(user_id):
    rows = UserBadge.query.filter_by(user_id=user_id).order_by(UserBadge.awarded_at.desc()).all()
    return [r.to_dict() for r in rows]


# ── the purchase hook ────────────────────────────────────────────────────────

def record_activity(transaction):
    """Call once per successful purchase, alongside record_purchase() /
    award_cashback(). Awards XP for the spend, advances any matching daily/
    weekly missions, and checks for newly-earned badges. Never raises —
    a gamification bug must never block or roll back a real purchase."""
    try:
        cfg = get_config()
        if not cfg.is_enabled or not transaction or transaction.status != 'success':
            return
        if not transaction.amount or transaction.amount <= 0:
            return

        xp_amount = round(transaction.amount / 100.0 * cfg.xp_per_100_naira_spent)
        if xp_amount > 0:
            award_xp(transaction.user_id, xp_amount, 'purchase', f'{transaction.type} purchase')

        _update_mission_progress(transaction.user_id, transaction)
        _check_badges(transaction.user_id, transaction)
    except Exception:
        logger.exception('[Gamification] record_activity failed (non-fatal, purchase unaffected)')


# ── leaderboard ──────────────────────────────────────────────────────────────

def get_leaderboard(limit=20):
    rows = (
        db.session.query(UserXP, User)
        .join(User, User.id == UserXP.user_id)
        .order_by(UserXP.total_xp.desc())
        .limit(limit)
        .all()
    )
    return [
        {'rank': i + 1, 'user_id': u.id, 'name': u.name, 'total_xp': xp.total_xp or 0,
         'level': xp.current_level or 1}
        for i, (xp, u) in enumerate(rows)
    ]


def get_user_rank(user_id):
    xp_row = get_or_create_user_xp(user_id)
    higher_count = UserXP.query.filter(UserXP.total_xp > (xp_row.total_xp or 0)).count()
    return higher_count + 1


# ── summary (everything one screen needs in one call) ───────────────────────

def get_user_summary(user_id):
    xp_row = get_or_create_user_xp(user_id)
    db.session.commit()
    current_level = _compute_level(xp_row.total_xp or 0)
    levels = get_levels()
    next_level = next((l for l in levels if l.level_number == (current_level.level_number + 1)), None) if current_level else None

    return {
        'total_xp': xp_row.total_xp or 0,
        'current_level': current_level.to_dict() if current_level else None,
        'next_level': next_level.to_dict() if next_level else None,
        'xp_to_next_level': (max(0, next_level.xp_required - xp_row.total_xp) if next_level else 0),
        'rank': get_user_rank(user_id),
        'badge_count': UserBadge.query.filter_by(user_id=user_id).count(),
    }


# ── admin ────────────────────────────────────────────────────────────────────

def get_platform_stats():
    total_users_with_xp = UserXP.query.count()
    total_xp_awarded = db.session.query(db.func.coalesce(db.func.sum(XPEntry.amount), 0)).scalar() or 0
    total_badges_awarded = UserBadge.query.count()
    missions_completed = UserMissionProgress.query.filter_by(is_completed=True).count()

    return {
        'total_users_with_xp':    total_users_with_xp,
        'total_xp_awarded':       total_xp_awarded,
        'total_badges_awarded':   total_badges_awarded,
        'missions_completed':     missions_completed,
    }


def admin_adjust_xp(user_id, amount, note=None):
    if amount == 0:
        return {'status': 'error', 'message': 'Amount cannot be zero'}
    if amount > 0:
        result = award_xp(user_id, amount, 'admin_adjust', note or 'Admin adjustment')
        db.session.commit()
        return {'status': 'success', 'data': result}
    xp_row = get_or_create_user_xp(user_id)
    xp_row.total_xp = max(0, (xp_row.total_xp or 0) + amount)  # amount negative here
    new_level_obj = _compute_level(xp_row.total_xp)
    xp_row.current_level = new_level_obj.level_number if new_level_obj else 1
    db.session.add(XPEntry(user_id=user_id, amount=amount, source='admin_adjust', description=note or 'Admin adjustment'))
    db.session.commit()
    return {'status': 'success', 'data': {'total_xp': xp_row.total_xp, 'new_level': xp_row.current_level}}
