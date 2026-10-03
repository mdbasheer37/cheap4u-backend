"""
Admin helper — run from the Render Shell (service > Shell), in the project folder:

    python user_tool.py find  zubairu            # search users by part of email/name/phone
    python user_tool.py check you@mail.com 'password'   # does this password work?
    python user_tool.py reset you@mail.com 'NewPass123' # set a new password

Never prints password hashes.
"""
import sys
from sqlalchemy import func
from app import app
from models import db, User


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
