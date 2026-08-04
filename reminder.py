# reminder.py — Bill Reminder: core business logic
#
# Self-contained, following the same shape as cashback.py / spin.py /
# card.py. Dispatch goes through notification_channels.py so this file
# never needs to know how (or whether) a channel actually delivers.

import calendar
import logging
from datetime import datetime, date, timedelta

from models import db, User
from reminder_models import ReminderConfig, BillReminder, ReminderLog, BILL_TYPES
import notification_channels

logger = logging.getLogger(__name__)

BILL_TYPE_LABELS = {
    'dstv': 'DSTV', 'gotv': 'GOtv', 'startimes': 'StarTimes',
    'electricity': 'Electricity', 'internet': 'Internet',
}


def get_config():
    cfg = ReminderConfig.query.get(1)
    if not cfg:
        cfg = ReminderConfig(id=1)
        db.session.add(cfg)
        db.session.flush()
    return cfg


def compute_next_due_date(due_day_of_month, from_date=None):
    """Returns the next date matching due_day_of_month that is >= from_date
    (or > from_date if they'd be equal but from_date has already passed
    today, handled by the caller). Clamps to the last day of a short month
    (e.g. day 31 in February -> Feb 28/29)."""
    base = from_date or date.today()
    year, month = base.year, base.month

    def _clamped(y, m, d):
        last_day = calendar.monthrange(y, m)[1]
        return date(y, m, min(d, last_day))

    candidate = _clamped(year, month, due_day_of_month)
    if candidate < base:
        month += 1
        if month > 12:
            month = 1
            year += 1
        candidate = _clamped(year, month, due_day_of_month)
    return candidate


def _advance_one_month(d, due_day_of_month):
    year, month = d.year, d.month + 1
    if month > 12:
        month = 1
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(due_day_of_month, last_day))


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_reminders(user_id, active_only=False):
    q = BillReminder.query.filter_by(user_id=user_id)
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(BillReminder.next_due_date.asc()).all()


def get_reminder(user_id, reminder_id):
    return BillReminder.query.filter_by(id=reminder_id, user_id=user_id).first()


def create_reminder(user_id, data):
    bill_type = data.get('bill_type')
    if bill_type not in BILL_TYPES:
        return None, f'bill_type must be one of {BILL_TYPES}'

    account_identifier = (data.get('account_identifier') or '').strip()
    if not account_identifier:
        return None, 'account_identifier is required'

    try:
        due_day = int(data.get('due_day_of_month'))
        if not (1 <= due_day <= 31):
            raise ValueError
    except (TypeError, ValueError):
        return None, 'due_day_of_month must be a number from 1 to 31'

    channels = data.get('channels')
    if channels:
        if isinstance(channels, list):
            channels = [c for c in channels if c in notification_channels.CHANNELS]
        else:
            channels = [c.strip() for c in str(channels).split(',') if c.strip() in notification_channels.CHANNELS]
        channels_str = ','.join(channels) if channels else None
    else:
        channels_str = None

    reminder = BillReminder(
        user_id=user_id, bill_type=bill_type,
        nickname=(data.get('nickname') or '').strip() or None,
        account_identifier=account_identifier,
        estimated_amount=_safe_float(data.get('estimated_amount')),
        due_day_of_month=due_day,
        next_due_date=compute_next_due_date(due_day),
        reminder_days_before=_safe_int(data.get('reminder_days_before')),
        channels=channels_str,
        is_recurring=bool(data.get('is_recurring', True)),
        is_active=bool(data.get('is_active', True)),
    )
    db.session.add(reminder)
    db.session.commit()
    return reminder, None


def _safe_float(val):
    if val in (None, ''):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val):
    if val in (None, ''):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def update_reminder(user_id, reminder_id, data):
    reminder = get_reminder(user_id, reminder_id)
    if not reminder:
        return None, 'Reminder not found'

    if 'nickname' in data:
        reminder.nickname = (data['nickname'] or '').strip() or None
    if 'account_identifier' in data:
        val = (data['account_identifier'] or '').strip()
        if not val:
            return None, 'account_identifier cannot be empty'
        reminder.account_identifier = val
    if 'estimated_amount' in data:
        reminder.estimated_amount = _safe_float(data['estimated_amount'])
    if 'due_day_of_month' in data:
        try:
            due_day = int(data['due_day_of_month'])
            if not (1 <= due_day <= 31):
                raise ValueError
            reminder.due_day_of_month = due_day
            reminder.next_due_date = compute_next_due_date(due_day)
        except (TypeError, ValueError):
            return None, 'due_day_of_month must be a number from 1 to 31'
    if 'reminder_days_before' in data:
        reminder.reminder_days_before = _safe_int(data['reminder_days_before'])
    if 'channels' in data:
        channels = data['channels']
        if channels:
            if isinstance(channels, list):
                channels = [c for c in channels if c in notification_channels.CHANNELS]
            else:
                channels = [c.strip() for c in str(channels).split(',') if c.strip() in notification_channels.CHANNELS]
            reminder.channels = ','.join(channels) if channels else None
        else:
            reminder.channels = None
    if 'is_recurring' in data:
        reminder.is_recurring = bool(data['is_recurring'])
    if 'is_active' in data:
        reminder.is_active = bool(data['is_active'])

    db.session.commit()
    return reminder, None


def delete_reminder(user_id, reminder_id):
    reminder = get_reminder(user_id, reminder_id)
    if not reminder:
        return False, 'Reminder not found'
    db.session.delete(reminder)
    db.session.commit()
    return True, None


# ── dispatch / polling ──────────────────────────────────────────────────────

def _build_message(reminder):
    label = BILL_TYPE_LABELS.get(reminder.bill_type, reminder.bill_type.title())
    name = reminder.nickname or label
    days_left = (reminder.next_due_date - date.today()).days
    when = "today" if days_left <= 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
    amount_part = f" (₦{reminder.estimated_amount:,.2f})" if reminder.estimated_amount else ""
    return f"{name} is due {when}{amount_part} — account {reminder.account_identifier}"


def check_and_dispatch_due_reminders():
    """
    Scheduled job (daily). Finds reminders that have entered their
    reminder window and haven't already been reminded for this due-date
    cycle, dispatches on each configured channel, and logs the outcome.
    Idempotent — `last_reminded_for_date` guards against double-sends if
    run twice in the same day.
    """
    cfg = get_config()
    if not cfg.is_enabled:
        return {'checked': 0, 'dispatched': 0}

    today = date.today()
    candidates = BillReminder.query.filter(
        BillReminder.is_active.is_(True),
        BillReminder.next_due_date >= today,
    ).all()

    dispatched = 0
    for reminder in candidates:
        days_before = reminder.reminder_days_before
        if days_before is None:
            days_before = cfg.default_reminder_days_before
        window_start = reminder.next_due_date - timedelta(days=days_before)

        if today < window_start:
            continue
        if reminder.last_reminded_for_date == reminder.next_due_date:
            continue  # already reminded for this cycle

        user = User.query.get(reminder.user_id)
        if not user:
            continue

        message = _build_message(reminder)
        channels = (reminder.channels.split(',') if reminder.channels
                    else (cfg.default_channels.split(',') if cfg.default_channels else ['push']))

        for channel_name in channels:
            status, detail = notification_channels.dispatch(channel_name, user, reminder, message)
            db.session.add(ReminderLog(
                reminder_id=reminder.id, user_id=user.id, channel=channel_name,
                status=status, message=message,
            ))

        reminder.last_reminded_at = datetime.utcnow()
        reminder.last_reminded_for_date = reminder.next_due_date
        dispatched += 1

    db.session.commit()
    return {'checked': len(candidates), 'dispatched': dispatched}


def advance_past_due_reminders():
    """Scheduled job (daily). Rolls next_due_date forward for recurring
    reminders whose due date has passed, so the cycle repeats monthly."""
    today = date.today()
    past_due = BillReminder.query.filter(
        BillReminder.is_recurring.is_(True),
        BillReminder.is_active.is_(True),
        BillReminder.next_due_date < today,
    ).all()

    for reminder in past_due:
        next_date = reminder.next_due_date
        # Guard against a long-idle reminder needing more than one bump
        for _ in range(24):
            next_date = _advance_one_month(next_date, reminder.due_day_of_month)
            if next_date >= today:
                break
        reminder.next_due_date = next_date
        reminder.last_reminded_for_date = None

    db.session.commit()
    return {'advanced': len(past_due)}


def get_pending_push(user_id):
    """Reminder logs waiting to be shown as an on-device notification."""
    return (
        ReminderLog.query
        .filter_by(user_id=user_id, channel='push', status='pending')
        .order_by(ReminderLog.created_at.desc())
        .all()
    )


def ack_push(user_id, log_id):
    """Client calls this after actually displaying the local notification,
    so it isn't shown again on the next poll."""
    log = ReminderLog.query.filter_by(id=log_id, user_id=user_id, channel='push').first()
    if not log:
        return False, 'Notification not found'
    log.status = 'sent'
    log.delivered_at = datetime.utcnow()
    db.session.commit()
    return True, None


def get_history(user_id, reminder_id=None, page=1, per_page=30):
    per_page = min(per_page, 100)
    q = ReminderLog.query.filter_by(user_id=user_id)
    if reminder_id:
        q = q.filter_by(reminder_id=reminder_id)
    q = q.order_by(ReminderLog.created_at.desc())
    total = q.count()
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    pages = (total + per_page - 1) // per_page if per_page else 1
    return [r.to_dict() for r in rows], total, pages


# ── scheduler ────────────────────────────────────────────────────────────────

_scheduler_started = False


def start_scheduler(app):
    global _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning('APScheduler not installed — bill reminder auto-dispatch disabled.')
        return

    def _job():
        with app.app_context():
            try:
                result = check_and_dispatch_due_reminders()
                advance_result = advance_past_due_reminders()
                if result.get('dispatched') or advance_result.get('advanced'):
                    logger.info(f'[Reminder] daily sweep: dispatched={result} advanced={advance_result}')
            except Exception:
                logger.exception('[Reminder] scheduled sweep failed')

    scheduler = BackgroundScheduler(daemon=True, timezone='UTC')
    scheduler.add_job(_job, 'interval', hours=6, next_run_time=datetime.utcnow() + timedelta(seconds=60))
    scheduler.start()
    _scheduler_started = True


# ── admin ────────────────────────────────────────────────────────────────────

def get_platform_stats():
    total_reminders = BillReminder.query.count()
    active = BillReminder.query.filter_by(is_active=True).count()
    by_type = (
        db.session.query(BillReminder.bill_type, db.func.count(BillReminder.id))
        .group_by(BillReminder.bill_type).all()
    )
    total_dispatched = ReminderLog.query.count()
    not_configured = ReminderLog.query.filter_by(status='not_configured').count()

    return {
        'total_reminders':    total_reminders,
        'active_reminders':   active,
        'by_bill_type':       [{'bill_type': t, 'count': c} for t, c in by_type],
        'total_dispatched':   total_dispatched,
        'undelivered_stub_channels': not_configured,   # SMS/email attempts with no real provider yet
    }
