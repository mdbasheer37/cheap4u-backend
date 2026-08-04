# reminder_models.py — Bill Reminder data models
#
# Uses the SAME `db` SQLAlchemy instance as models.py so these tables are
# created automatically by the existing `db.create_all()` call in app.py.

from datetime import datetime
from models import db

BILL_TYPES = ('dstv', 'gotv', 'startimes', 'electricity', 'internet')


class ReminderConfig(db.Model):
    """Singleton settings row (id is always 1) controlling the whole Bill
    Reminder feature. Editable from the admin panel."""
    __tablename__ = 'reminder_config'

    id                             = db.Column(db.Integer, primary_key=True)
    is_enabled                     = db.Column(db.Boolean, default=True)
    default_reminder_days_before   = db.Column(db.Integer, default=3)
    default_channels               = db.Column(db.String(50), default='push')   # comma-separated

    # These flags reflect whether a real SMS/email provider has been wired
    # up in notification_channels.py — see that file's docstring. Toggling
    # them off here just stops the feature from *claiming* delivery it
    # can't yet make; it doesn't disable anything by itself.
    sms_provider_configured        = db.Column(db.Boolean, default=False)
    email_provider_configured      = db.Column(db.Boolean, default=False)

    updated_at                      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'is_enabled':                    self.is_enabled,
            'default_reminder_days_before':  self.default_reminder_days_before,
            'default_channels':              self.default_channels.split(',') if self.default_channels else [],
            'sms_provider_configured':       self.sms_provider_configured,
            'email_provider_configured':     self.email_provider_configured,
        }


class BillReminder(db.Model):
    """One recurring bill a user wants to be reminded about."""
    __tablename__ = 'bill_reminders'

    id                    = db.Column(db.Integer, primary_key=True)
    user_id               = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    bill_type             = db.Column(db.String(20), nullable=False)     # see BILL_TYPES
    nickname              = db.Column(db.String(60), nullable=True)      # e.g. "Home DSTV"
    account_identifier    = db.Column(db.String(50), nullable=False)     # smartcard/meter/account number
    estimated_amount      = db.Column(db.Float, nullable=True)

    due_day_of_month      = db.Column(db.Integer, nullable=False)        # 1–31
    next_due_date         = db.Column(db.Date, nullable=False, index=True)

    reminder_days_before  = db.Column(db.Integer, nullable=True)         # None = use ReminderConfig default
    channels               = db.Column(db.String(50), nullable=True)      # None = use ReminderConfig default

    is_recurring            = db.Column(db.Boolean, default=True)         # monthly
    is_active                = db.Column(db.Boolean, default=True)

    last_reminded_at         = db.Column(db.DateTime, nullable=True)
    last_reminded_for_date   = db.Column(db.Date, nullable=True)   # prevents duplicate same-cycle reminders

    created_at                = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at                = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id':                     self.id,
            'bill_type':              self.bill_type,
            'nickname':               self.nickname,
            'account_identifier':     self.account_identifier,
            'estimated_amount':       self.estimated_amount,
            'due_day_of_month':       self.due_day_of_month,
            'next_due_date':          self.next_due_date.isoformat() if self.next_due_date else None,
            'reminder_days_before':   self.reminder_days_before,
            'channels':               self.channels.split(',') if self.channels else None,
            'is_recurring':           self.is_recurring,
            'is_active':              self.is_active,
            'last_reminded_at':       self.last_reminded_at.isoformat() if self.last_reminded_at else None,
        }


class ReminderLog(db.Model):
    """Every time a reminder was (or attempted to be) dispatched, on any
    channel. Gives the admin panel visibility into delivery, and gives the
    app something to poll for pending push notifications."""
    __tablename__ = 'reminder_logs'

    id           = db.Column(db.Integer, primary_key=True)
    reminder_id  = db.Column(db.Integer, db.ForeignKey('bill_reminders.id'), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    channel      = db.Column(db.String(10), nullable=False)   # 'push' | 'sms' | 'email'
    # 'sent' (channel confirms delivery/queued), 'pending' (awaiting client
    # pickup — used by push), 'failed', 'not_configured' (channel has no
    # real provider wired up — see notification_channels.py)
    status       = db.Column(db.String(20), default='pending')
    message      = db.Column(db.String(255), nullable=True)

    delivered_at  = db.Column(db.DateTime, nullable=True)   # set when the client acks a push
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            'id':            self.id,
            'reminder_id':   self.reminder_id,
            'channel':       self.channel,
            'status':        self.status,
            'message':       self.message,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
        }
