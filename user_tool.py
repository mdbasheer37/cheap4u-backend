"""
Admin helper — run from the Render Shell (service > Shell), in the project folder:

    python user_tool.py find  zubairu            # search users by part of email/name/phone
    python user_tool.py check you@mail.com 'password'   # does this password work?
    python user_tool.py reset you@mail.com 'NewPass123' # set a new password
    python user_tool.py demo  review@cheap4u.app 'Review#2026' [login_pin] [txn_pin] [wallet_balance]
        -> creates (or repairs) a ready-to-use, already-verified account for the
           Google Play reviewers: password login works with NO SMS code.
           Defaults: login PIN 246810, transaction PIN 135790, wallet balance 0.

Never prints password hashes.
"""
import sys
from sqlalchemy import func
from app import app
from models import db, User
from utils import generate_referral_code


def _free_phone():
    """A made-up, unused phone number for the review account (it never receives SMS)."""
    n = 8000000001
    while User.query.filter_by(phone=f"0{n}").first():
        n += 1
    return f"0{n}"


def create_demo_account(email, extra):
    """Create or repair a verified review account. Must run inside app.app_context()."""
    if len(extra) < 1 or len(extra[0]) < 6:
        print("Usage: python user_tool.py demo EMAIL PASSWORD [login_pin] [txn_pin] [wallet_balance]")
        print("PASSWORD must be at least 6 characters."); return
    password = extra[0].strip()
    login_pin = extra[1].strip() if len(extra) > 1 else "246810"
    txn_pin   = extra[2].strip() if len(extra) > 2 else "135790"
    try:
        balance = float(extra[3]) if len(extra) > 3 else None
    except ValueError:
        print("wallet_balance must be a number, e.g. 500"); return
    for label, pin in (("login_pin", login_pin), ("txn_pin", txn_pin)):
        if not (pin.isdigit() and 4 <= len(pin) <= 6):
            print(f"{label} must be 4-6 digits."); return

    user = User.query.filter(func.lower(User.email) == email).first()
    created = user is None
    if created:
        user = User(name="Google Play Reviewer", email=email, phone=_free_phone(),
                    referral_code=generate_referral_code())
        db.session.add(user)
    user.set_password(password)
    user.set_login_pin(login_pin)
    user.set_transaction_pin(txn_pin)
    user.is_verified = True          # no SMS code needed to sign in
    user.is_active = True
    if balance is not None:
        user.wallet_balance = balance
    elif created:
        user.wallet_balance = 0.0
    db.session.commit()

    ok = user.check_password(password)
    print("-" * 52)
    print(f"{'CREATED' if created else 'UPDATED'} review account (id={user.id})")
    print(f"  Email           : {user.email}")
    print(f"  Password        : {password}      (check: {'OK' if ok else 'FAILED'})")
    print(f"  Login PIN       : {login_pin}")
    print(f"  Transaction PIN : {txn_pin}")
    print(f"  Wallet balance  : N{user.wallet_balance:,.2f}")
    print(f"  Verified/Active : {user.is_verified}/{user.is_active}")
    print("-" * 52)
    print("Paste these into Play Console > App content > App access.")


def main():
    if len(sys.argv) < 3:
        print(__doc__); return
    cmd, ident = sys.argv[1], sys.argv[2].strip().lower()
    with app.app_context():
        if cmd == "find":
            like = f"%{ident}%"
            rows = User.query.filter(
                func.lower(User.email).like(like) | func.lower(User.name).like(like) | User.phone.like(like)
            ).all()
            if not rows:
                print("No matching users."); return
            for u in rows:
                print(f"id={u.id} email={u.email!r} phone={u.phone} verified={u.is_verified} "
                      f"active={u.is_active} pin_set={bool(getattr(u, 'login_pin_hash', None))}")
            return
        if cmd == "demo":
            create_demo_account(ident, sys.argv[3:]); return
        rows = User.query.filter(func.lower(User.email) == ident).all()
        if not rows:
            print(f"NO ACCOUNT with email {ident!r}. Use 'find' to look for the right spelling."); return
        if cmd == "check":
            pw = sys.argv[3]
            for u in rows:
                print(f"id={u.id}: password {'MATCHES' if u.check_password(pw) else 'does NOT match'}"
                      f" | verified={u.is_verified} active={u.is_active}")
        elif cmd == "reset":
            pw = sys.argv[3]
            if len(pw) < 6:
                print("Password must be at least 6 characters."); return
            for u in rows:
                u.set_password(pw)
                print(f"id={u.id}: password reset")
            db.session.commit()
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
