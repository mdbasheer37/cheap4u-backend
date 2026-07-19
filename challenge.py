# challenge.py — Monthly Champion Challenge: core business logic
#
# This module is intentionally self-contained so it can be dropped into the
# existing backend without touching models.py. It is imported by:
#   - cheapdatahub.py / vtunaija.py / airtime_to_cash.py  → record_purchase()
#   - challenge_routes.py                                  → everything else
#   - app.py                                                → start_scheduler()

import logging
import calendar
from datetime import datetime, timedelta

from models import db, User, Transaction, Profit
from challenge_models import (
    ChallengeConfig, ChallengeEntry, ChallengeWinner, ChallengeNotification,
)

logger = logging.getLogger(__name__)

# Every one of these Transaction.type values counts toward the monthly total.
# Deliberately a whitelist (not "everything that isn't excluded") so a new
# internal transaction type (wallet_funding, withdrawal, admin adjustments,
# referral payouts, the challenge's own reward payout, etc.) never leaks in
# by accident. Add a new paid service here when one is added to the app —
# e.g. "gift_card" the day that service goes live.
PURCHASE_TYPES = {
    'airtime',
    'data',
    'electricity',
    'cable_tv',
    'exam_pin',
    'airtime_to_cash',
    'gift_card',
}


# ── helpers ─────────────────────────────────────────────────────────────────

def _month_key(dt=None):
    dt = dt or datetime.utcnow()
    return dt.strftime('%Y-%m')


def _month_bounds(month_key):
    """Return (start_datetime, end_datetime_exclusive) for a 'YYYY-MM' key."""
    year, month = (int(p) for p in month_key.split('-'))
    start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = start + timedelta(days=last_day)
    return start, end


def seconds_until_month_end(now=None):
    now = now or datetime.utcnow()
    _, end = _month_bounds(_month_key(now))
    return max(0, int((end - now).total_seconds()))


def get_config():
    cfg = ChallengeConfig.query.get(1)
    if not cfg:
        cfg = ChallengeConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def _ordered_entries(month):
    """Full ranked list for a month: highest total first; ties broken by
    who reached that total first (earlier updated_at wins)."""
    return (
        ChallengeEntry.query
        .filter_by(month=month)
        .order_by(ChallengeEntry.total_amount.desc(), ChallengeEntry.updated_at.asc())
        .all()
    )


def _rank_of(user_id, ordered):
    for idx, e in enumerate(ordered, start=1):
        if e.user_id == user_id:
            return idx
    return None


# ── notifications ────────────────────────────────────────────────────────────

def _notify(user_id, ntype, title, message):
    db.session.add(ChallengeNotification(
        user_id=user_id, type=ntype, title=title, message=message,
    ))


def _maybe_notify_rank_change(entry, old_rank, new_rank):
    """Fires the '#1' / 'Top 3' / 'Top 10' push notifications the first
    time a user crosses into that band. Scoped to the user who just made
    the purchase — other users' ranks may also shift, but re-evaluating
    the whole board on every single transaction isn't worth the cost."""
    if new_rank is None:
        return
    improved = old_rank is None or new_rank < old_rank
    if not improved:
        return

    if new_rank == 1 and entry.last_notified_rank != 1:
        _notify(entry.user_id, 'first_place', "🥇 You're #1!",
                "You're now leading the Monthly Champion Challenge! Stay on top to win 50% cashback on your total spend.")
    elif new_rank <= 3 and (old_rank is None or old_rank > 3):
        _notify(entry.user_id, 'top3', "🥉 You made Top 3!",
                f"You've entered the Top 3 of the Monthly Champion Challenge at Rank #{new_rank}.")
    elif new_rank <= 10 and (old_rank is None or old_rank > 10):
        _notify(entry.user_id, 'top10', "🏆 You're in the Top 10!",
                f"You've entered the Top 10 of the Monthly Champion Challenge at Rank #{new_rank}.")

    if new_rank < (entry.last_notified_rank or 999999):
        entry.last_notified_rank = new_rank


# ── the hook called from the purchase flows ──────────────────────────────────

def record_purchase(transaction):
    """
    Call this once a Transaction has been set to status='success', BEFORE
    the caller's final db.session.commit(). It only adds/updates rows in
    the current session — it does not commit — so it rides along with the
    same atomic commit as the purchase itself.

    Safe to call unconditionally: it no-ops for non-qualifying transaction
    types, and any unexpected error is swallowed (a challenge-tracking bug
    must never block or roll back a real, paid-for purchase).
    """
    try:
        if not transaction or transaction.type not in PURCHASE_TYPES:
            return
        if not transaction.amount or transaction.amount <= 0:
            return

        cfg = get_config()
        if not cfg.is_enabled:
            return

        month = _month_key(transaction.created_at or datetime.utcnow())

        entry = (
            ChallengeEntry.query
            .filter_by(user_id=transaction.user_id, month=month)
            .with_for_update(read=False)
            .first()
        )
        if not entry:
            entry = ChallengeEntry(
                user_id=transaction.user_id, month=month,
                total_amount=0.0, purchase_count=0, last_notified_rank=999999,
            )
            db.session.add(entry)
            db.session.flush()

        old_rank = _rank_of(transaction.user_id, _ordered_entries(month))

        entry.total_amount   = round((entry.total_amount or 0) + transaction.amount, 2)
        entry.purchase_count = (entry.purchase_count or 0) + 1
        entry.updated_at     = datetime.utcnow()
        db.session.flush()

        new_rank = _rank_of(transaction.user_id, _ordered_entries(month))
        _maybe_notify_rank_change(entry, old_rank, new_rank)

    except Exception:
        logger.exception('challenge.record_purchase failed (non-fatal)')


# ── read APIs used by the routes ─────────────────────────────────────────────

def get_leaderboard(month=None, limit=100):
    month = month or _month_key()
    ordered = _ordered_entries(month)
    if not ordered:
        return [], month

    leader_total = ordered[0].total_amount or 0.0
    reward_labels = {1: '🥇 1st Place', 2: '🥈 2nd Place', 3: '🥉 3rd Place'}

    board = []
    for idx, e in enumerate(ordered[:limit], start=1):
        user = e.user or User.query.get(e.user_id)
        progress = 0.0
        if leader_total > 0:
            progress = round(min(100.0, (e.total_amount / leader_total) * 100), 1)
        board.append({
            'rank':            idx,
            'user_id':         e.user_id,
            'name':            user.name if user else 'Unknown',
            'avatar_initial':  (user.name[:1].upper() if user and user.name else '?'),
            'total_amount':    round(e.total_amount or 0, 2),
            'purchase_count':  e.purchase_count,
            'reward_position': reward_labels.get(idx),
            'progress_percent': progress,
        })
    return board, month


def get_user_summary(user_id, month=None):
    month = month or _month_key()
    ordered = _ordered_entries(month)
    rank = _rank_of(user_id, ordered)
    my_entry = next((e for e in ordered if e.user_id == user_id), None)
    my_total = my_entry.total_amount if my_entry else 0.0

    amount_to_overtake_next = None
    if rank and rank > 1:
        ahead = ordered[rank - 2]  # the entry directly above (0-indexed)
        amount_to_overtake_next = round(max(0.0, (ahead.total_amount - my_total) + 0.01), 2)
    elif rank == 1:
        amount_to_overtake_next = 0.0

    reward_labels = {1: '🥇 1st Place', 2: '🥈 2nd Place', 3: '🥉 3rd Place'}
    cfg = get_config()

    return {
        'challenge_enabled':          cfg.is_enabled,
        'month':                      month,
        'rank':                       rank,
        'total_participants':         len(ordered),
        'total_monthly_purchases':    round(my_total, 2),
        'reward_position':            reward_labels.get(rank) if rank else None,
        'amount_to_overtake_next':    amount_to_overtake_next,
        'countdown_seconds':         seconds_until_month_end(),
    }


def get_winners_history(page=1, per_page=30):
    q = (
        ChallengeWinner.query
        .order_by(ChallengeWinner.month.desc(), ChallengeWinner.rank.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )
    return [w.to_dict() for w in q.items], q.total, q.pages


def get_notifications(user_id, unread_only=False, limit=50):
    q = ChallengeNotification.query.filter_by(user_id=user_id)
    if unread_only:
        q = q.filter_by(is_read=False)
    rows = q.order_by(ChallengeNotification.created_at.desc()).limit(limit).all()
    return [n.to_dict() for n in rows]


def mark_notifications_read(user_id, notification_id=None):
    q = ChallengeNotification.query.filter_by(user_id=user_id, is_read=False)
    if notification_id:
        q = q.filter_by(id=notification_id)
    q.update({'is_read': True})
    db.session.commit()


# ── month-end processing: archive winners + credit wallets ──────────────────

def _credit_reward(user, winner_row, reward_amount):
    """Credits a winner's wallet and logs a visible Transaction row."""
    user.wallet_balance = round((user.wallet_balance or 0) + reward_amount, 2)
    tx = Transaction(
        user_id=user.id,
        reference=f"CHALLENGE_{winner_row.month}_{winner_row.rank}_{user.id}",
        type='challenge_reward',
        service_type='monthly_challenge',
        amount=reward_amount,
        profit=0.0,
        status='success',
        details={
            'month': winner_row.month,
            'rank': winner_row.rank,
            'reward_type': winner_row.reward_type,
        },
    )
    db.session.add(tx)
    winner_row.credited = True
    winner_row.credited_at = datetime.utcnow()

    _notify(
        user.id, 'reward_credited', '💰 Reward Credited!',
        f"₦{reward_amount:,.2f} has been credited to your wallet for finishing "
        f"Rank #{winner_row.rank} in the {winner_row.month} Monthly Champion Challenge!",
    )


def process_month_end(month_key=None, force=False):
    """
    Archives the Top 3 for `month_key` (defaults to the month that just
    ended) into ChallengeWinner, credits their wallets, and notifies all
    Top-10 participants that the challenge has ended. Idempotent — safe to
    call more than once for the same month unless `force=True` is passed to
    intentionally recompute (existing winner rows for that month are left
    untouched either way; use the admin manual-credit endpoint for
    corrections instead of forcing).

    Returns a summary dict.
    """
    month_key = month_key or _month_key(datetime.utcnow() - timedelta(days=1))

    already_done = ChallengeWinner.query.filter_by(month=month_key).first()
    if already_done and not force:
        return {'status': 'skipped', 'reason': 'already processed', 'month': month_key}

    cfg = get_config()
    ordered = _ordered_entries(month_key)

    # Notify everyone who made the Top 10 that the challenge has ended,
    # regardless of whether the feature is currently enabled (so users
    # still get closure on a challenge they participated in).
    for idx, entry in enumerate(ordered[:10], start=1):
        _notify(
            entry.user_id, 'challenge_ended', '🏁 Monthly Challenge Ended',
            f"The {month_key} Monthly Champion Challenge has ended. "
            f"You finished Rank #{idx} with ₦{entry.total_amount:,.2f} in purchases.",
        )

    winners_created = []
    if cfg.is_enabled:
        rewards = [
            (1, cfg.first_place_percent, 'cashback'),
            (2, cfg.second_place_bonus, 'bonus'),
            (3, cfg.third_place_bonus, 'bonus'),
        ]
        for rank, reward_value, reward_type in rewards:
            if len(ordered) < rank:
                continue
            entry = ordered[rank - 1]
            if entry.total_amount < (cfg.min_qualifying_amount or 0):
                continue

            reward_amount = (
                round(entry.total_amount * (reward_value / 100.0), 2)
                if reward_type == 'cashback' else round(reward_value, 2)
            )
            user = User.query.get(entry.user_id)
            if not user:
                continue

            winner_row = ChallengeWinner(
                month=month_key, rank=rank, user_id=user.id, user_name=user.name,
                total_amount=entry.total_amount, reward_amount=reward_amount,
                reward_type=reward_type, credited=False,
            )
            db.session.add(winner_row)
            db.session.flush()

            _credit_reward(user, winner_row, reward_amount)
            winners_created.append(winner_row.to_dict())

    cfg.last_processed_month = month_key
    db.session.commit()

    logger.info(f'[Challenge] Month {month_key} processed — {len(winners_created)} winners credited')
    return {'status': 'success', 'month': month_key, 'winners': winners_created}


def check_and_process_new_month():
    """
    Called by the scheduler (and safe to call from a manual admin endpoint
    too). If the calendar month has rolled over since the last time we
    processed, archives/credits the month that just ended. Idempotent.
    """
    cfg = get_config()
    current = _month_key()
    prev_month_key = _month_key(datetime.utcnow().replace(day=1) - timedelta(days=1))

    if cfg.last_processed_month == prev_month_key:
        return {'status': 'noop', 'reason': 'already up to date'}
    if cfg.last_processed_month == current:
        return {'status': 'noop', 'reason': 'same month'}

    return process_month_end(prev_month_key)


def manual_credit_winner(winner_id):
    """Admin action: (re)credit a specific winner row that wasn't credited
    automatically (e.g. wallet credit failed, or admin overrides)."""
    winner_row = ChallengeWinner.query.get(winner_id)
    if not winner_row:
        return {'status': 'error', 'message': 'Winner record not found'}
    if winner_row.credited:
        return {'status': 'error', 'message': 'Already credited'}
    user = User.query.get(winner_row.user_id)
    if not user:
        return {'status': 'error', 'message': 'User not found'}

    _credit_reward(user, winner_row, winner_row.reward_amount)
    db.session.commit()
    return {'status': 'success', 'message': f'₦{winner_row.reward_amount:,.2f} credited to {user.name}'}


# ── scheduler ─────────────────────────────────────────────────────────────

_scheduler_started = False


def start_scheduler(app):
    """
    Starts a lightweight background job that checks every hour whether the
    month has rolled over, and if so archives + credits last month's
    winners on the 1st. Call once from app.py after create_app().
    """
    global _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning('APScheduler not installed — monthly challenge auto-reset disabled. '
                        'Add "APScheduler" to requirements.txt.')
        return

    def _job():
        with app.app_context():
            try:
                result = check_and_process_new_month()
                if result.get('status') == 'success':
                    logger.info(f'[Challenge] Auto-processed month end: {result}')
            except Exception:
                logger.exception('[Challenge] scheduled month-end check failed')

    scheduler = BackgroundScheduler(daemon=True, timezone='UTC')
    # Runs hourly; process_month_end() is idempotent so this is safe even
    # if the dyno restarts or multiple checks land in the same hour.
    scheduler.add_job(_job, 'interval', hours=1, next_run_time=datetime.utcnow() + timedelta(seconds=30))
    scheduler.start()
    _scheduler_started = True
    logger.info('[Challenge] Monthly reset scheduler started (hourly check)')
