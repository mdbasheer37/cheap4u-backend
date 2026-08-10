"""Seed database with sample data for Makari Islamic TV"""
import os
import sys
import random
from datetime import datetime, timedelta

from database import SessionLocal, engine, Base
import models
from auth_utils import get_password_hash

def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # ── Categories ──────────────────────────────────────────
        categories_data = [
            {"name_en": "Tafsir",   "name_ha": "Tafsiri",         "slug": "tafsir",   "icon": "book-open",       "color": "#1a7a4a", "sort_order": 1},
            {"name_en": "Aqeedah",  "name_ha": "Akida",           "slug": "aqeedah",  "icon": "star",            "color": "#c9a227", "sort_order": 2},
            {"name_en": "Fiqhu",    "name_ha": "Fikihu",          "slug": "fiqhu",    "icon": "scale",           "color": "#2563eb", "sort_order": 3},
            {"name_en": "Hadith",   "name_ha": "Hadisi",          "slug": "hadith",   "icon": "scroll",          "color": "#7c3aed", "sort_order": 4},
            {"name_en": "Ramadan",  "name_ha": "Ramadan",         "slug": "ramadan",  "icon": "moon",            "color": "#0891b2", "sort_order": 5},
            {"name_en": "Marriage", "name_ha": "Aure",            "slug": "marriage", "icon": "heart",           "color": "#db2777", "sort_order": 6},
            {"name_en": "Family",   "name_ha": "Iyali",           "slug": "family",   "icon": "home",            "color": "#059669", "sort_order": 7},
            {"name_en": "Youth",    "name_ha": "Matasa",          "slug": "youth",    "icon": "users",           "color": "#d97706", "sort_order": 8},
            {"name_en": "Women",    "name_ha": "Mata",            "slug": "women",    "icon": "user",            "color": "#9333ea", "sort_order": 9},
            {"name_en": "Q & A",    "name_ha": "Tambaya da Amsa", "slug": "qna",      "icon": "message-circle",  "color": "#ef4444", "sort_order": 10},
        ]

        cats = []
        for cd in categories_data:
            existing = db.query(models.Category).filter(models.Category.slug == cd["slug"]).first()
            if not existing:
                cat = models.Category(**cd)
                db.add(cat)
                db.flush()
                cats.append(cat)
            else:
                cats.append(existing)
        db.commit()

        # ── Admin user ───────────────────────────────────────────
        admin = db.query(models.User).filter(models.User.email == "admin@makariilamictv.com").first()
        if not admin:
            admin = models.User(
                username="admin",
                email="admin@makariilamictv.com",
                full_name="Makari TV Admin",
                hashed_password=get_password_hash("admin123"),
                role=models.UserRole.admin,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            print("✅ Admin user created")

        # ── Lectures ─────────────────────────────────────────────
        lecture_titles = [
            ("Tafsirin Suratul Baqarah - Darasi na 1",    "Tafsir of Surah Al-Baqarah - Lesson 1"),
            ("Tafsirin Suratul Al-Imran",                  "Tafsir of Surah Al-Imran"),
            ("Muhimmancin Sallah a Musulunci",              "The Importance of Prayer in Islam"),
            ("Yadda Ake Yin Sallah daidai",                "How to Perform Prayer Correctly"),
            ("Ramadan: Wata na Albarku",                   "Ramadan: The Month of Blessings"),
            ("Aure a Musulunci - Kashi na 1",              "Marriage in Islam - Part 1"),
            ("Tarbiyya: Yadda Ake Reno Yara",              "Upbringing: How to Raise Children"),
            ("Aqeedar Musulunci - Tsarin Imani",           "Islamic Creed - Pillars of Faith"),
            ("Hadisai 40 na Imam Nawawi - Darasi 1",       "40 Hadiths of Imam Nawawi - Lesson 1"),
            ("Mata a Musulunci: Matsayinsu",               "Women in Islam: Their Status"),
            ("Matasa da Kalubalen Zamani",                  "Youth and Modern Challenges"),
            ("Tambayoyi da Amsoshi - Sashe 1",             "Questions and Answers - Part 1"),
            ("Zikiri da Addu'a",                           "Remembrance and Supplication"),
            ("Tafsirin Yasin",                             "Tafsir of Surah Yasin"),
            ("Halayen Annabi Muhammad SAW",                "Character of Prophet Muhammad SAW"),
            ("Zakka: Hukunce-hukuncenta",                  "Zakat: Its Rulings"),
            ("Azumi: Sharudda da Hukunce-hukuncen",        "Fasting: Conditions and Rulings"),
            ("Hajji da Umra",                              "Hajj and Umrah"),
            ("Taubah da Neman Gafara",                     "Repentance and Seeking Forgiveness"),
            ("Lahira: Duniyar Gobe",                       "Afterlife: The World to Come"),
        ]

        for i, (title_ha, title_en) in enumerate(lecture_titles):
            existing = db.query(models.Lecture).filter(models.Lecture.title_en == title_en).first()
            if existing:
                continue

            cat = cats[i % len(cats)]
            lec = models.Lecture(
                title_en=title_en,
                title_ha=title_ha,
                description_en=(
                    f"A comprehensive lecture on {title_en} by Malam Ibrahim Makari. "
                    "This lecture covers key Islamic teachings and practical guidance for Muslims."
                ),
                description_ha=(
                    f"Darasin {title_ha} wanda Malam Ibrahim Makari ya gabatar. "
                    "Wannan darasin ya ƙunshi mahimman koyarwar Musulunci."
                ),
                category_id=cat.id,
                thumbnail=f"https://picsum.photos/seed/{i+100}/400/225",
                duration=random.randint(1800, 7200),
                view_count=random.randint(100, 50000),
                like_count=random.randint(10, 5000),
                is_featured=(i < 5),
                is_trending=(i < 8),
                is_published=True,
                tags=f"makari,islamic,{cat.slug},lecture",
                date_recorded=datetime.now() - timedelta(days=random.randint(1, 365)),
            )
            db.add(lec)
            db.flush()

            # Demo YouTube video
            vid = models.Video(
                lecture_id=lec.id,
                title=title_en,
                youtube_url="https://www.youtube.com/embed/dQw4w9WgXcQ",
                stream_url="https://www.youtube.com/embed/dQw4w9WgXcQ",
                quality="720p",
                duration=lec.duration,
                is_downloadable=True,
            )
            db.add(vid)

            # Demo audio
            aud = models.AudioFile(
                lecture_id=lec.id,
                title=title_en,
                stream_url=f"https://www.soundhelix.com/examples/mp3/SoundHelix-Song-{(i % 17) + 1}.mp3",
                duration=lec.duration,
                bitrate=128,
                is_downloadable=True,
            )
            db.add(aud)

        db.commit()

        # ── Books ────────────────────────────────────────────────
        books_data = [
            {"title_en": "Fiqhus Sunnah",       "title_ha": "Fikhus Sunnah",   "author": "Sayyid Sabiq",     "category": "Fiqhu",   "language": "ar"},
            {"title_en": "Riyadus Salihin",      "title_ha": "Riyazus Salihin", "author": "Imam Nawawi",      "category": "Hadith",  "language": "ar"},
            {"title_en": "Fortress of the Muslim","title_ha": "Kushin Musulmi", "author": "Said Al-Qahtani", "category": "Dua",     "language": "en"},
            {"title_en": "Islamic Creed",        "title_ha": "Akidar Musulunci","author": "Dr. Umar Faruq",  "category": "Aqeedah", "language": "en"},
            {"title_en": "The Sealed Nectar",    "title_ha": "Tarihin Annabi",  "author": "Safi-ur-Rahman",  "category": "Seerah",  "language": "en"},
        ]
        for bd in books_data:
            existing = db.query(models.Book).filter(models.Book.title_en == bd["title_en"]).first()
            if not existing:
                seed_val = abs(hash(bd["title_en"])) % 1000
                book = models.Book(
                    **bd,
                    cover_image=f"https://picsum.photos/seed/{seed_val}/200/300",
                    description=f"A comprehensive Islamic book: {bd['title_en']}",
                    is_downloadable=True,
                )
                db.add(book)

        # ── Live streams ─────────────────────────────────────────
        if not db.query(models.LiveStream).first():
            for s in [
                {"title": "Makari Live TV",          "stream_type": "live_tv", "stream_url": "https://www.youtube.com/embed/live_stream", "is_live": False, "viewer_count": 0, "thumbnail": "https://picsum.photos/seed/live1/400/225"},
                {"title": "Friday Tafsir Live",      "stream_type": "tafsir",  "stream_url": "https://www.youtube.com/embed/live_stream", "is_live": False, "viewer_count": 0, "thumbnail": "https://picsum.photos/seed/live2/400/225"},
                {"title": "Ramadan Special Broadcast","stream_type": "ramadan", "stream_url": "https://www.youtube.com/embed/live_stream", "is_live": False, "viewer_count": 0, "thumbnail": "https://picsum.photos/seed/live3/400/225"},
            ]:
                db.add(models.LiveStream(**s))

        db.commit()
        print("✅ Database seeded successfully!")
        print("   Admin: admin@makariilamictv.com / admin123")

    except Exception as e:
        db.rollback()
        print(f"❌ Seed error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
