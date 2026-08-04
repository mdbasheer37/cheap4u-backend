# notification_channels.py — Bill Reminder: pluggable delivery channels
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS FILE EXISTS
# ─────────────────────────────────────────────────────────────────────────
# A bill reminder can go out on three kinds of channel, and each has a
# different honest starting point:
#
#   PUSH  — Works today. The Kivy app polls /api/reminders/pending and
#           shows a real local device notification (via plyer) when it
#           finds one due. No third-party push service (FCM/APNs) is
#           wired in — that needs a server key + device token registry
#           this project doesn't have yet — so "push" here means
#           on-device local notification, not a remote push while the app
#           is closed. Good enough for "open the app and see what's due,"
#           not for "buzz my phone at 2am while the app isn't running."
#
#   SMS   — Architecture-ready, not connected. SMSChannel below has the
#           exact shape a real send call needs (recipient, message) and
#           logs what it *would* send. Wiring up a real provider (Termii,
#           Africa's Talking, Twilio) is: implement send() with their API
#           call, done — nothing else in reminder.py changes.
#
#   EMAIL — Same story as SMS, via EmailChannel — implement send() with
#           real SMTP/provider creds, nothing else changes.
#
# reminder.py never calls a provider directly — it only calls
# dispatch(channel_name, reminder, user, message) below, exactly the same
# shape card.py uses for card_provider.py.
# ─────────────────────────────────────────────────────────────────────────

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class NotificationChannel(ABC):
    name = 'base'

    @abstractmethod
    def send(self, user, reminder, message):
        """Returns (status, detail) where status is 'sent' | 'pending' |
        'failed' | 'not_configured'."""
        raise NotImplementedError


class PushChannel(NotificationChannel):
    """Doesn't send anything itself — it marks the reminder log row as
    'pending' so the client's poll of /api/reminders/pending picks it up
    and fires a local notification via plyer. See reminder_routes.py's
    /pending and /pending/<id>/ack endpoints."""
    name = 'push'

    def send(self, user, reminder, message):
        return 'pending', 'Queued for on-device delivery on next app open'


class SMSChannel(NotificationChannel):
    """No SMS gateway is configured. Logs what would have been sent so the
    reminder pipeline is fully exercised end-to-end in development, and
    swapping in a real provider later is a one-function change."""
    name = 'sms'

    def send(self, user, reminder, message):
        if not user.phone:
            return 'failed', 'User has no phone number on file'
        logger.info(f'[SMS-not-configured] Would send to {user.phone}: {message}')
        return 'not_configured', f'SMS provider not connected — would have sent to {user.phone}'


class EmailChannel(NotificationChannel):
    """No SMTP/email provider is configured. Same honest stub as SMS."""
    name = 'email'

    def send(self, user, reminder, message):
        if not user.email:
            return 'failed', 'User has no email on file'
        logger.info(f'[Email-not-configured] Would send to {user.email}: {message}')
        return 'not_configured', f'Email provider not connected — would have sent to {user.email}'


CHANNELS = {
    'push': PushChannel(),
    'sms': SMSChannel(),
    'email': EmailChannel(),
}


def dispatch(channel_name, user, reminder, message):
    channel = CHANNELS.get(channel_name)
    if not channel:
        return 'failed', f'Unknown channel: {channel_name}'
    try:
        return channel.send(user, reminder, message)
    except Exception as e:
        logger.exception(f'[Reminder] channel {channel_name} raised an error')
        return 'failed', str(e)
