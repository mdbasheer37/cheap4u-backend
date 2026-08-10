from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os

from database import engine, Base, check_db_connection
from routers import (
    auth, lectures, videos, audio, categories,
    search, favorites, downloads, notifications,
    library, live, admin, users, prayer
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Test DB connection
    check_db_connection()

    # 2. Create all tables (safe — only creates what doesn't exist)
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables ready")

    # 3. Seed initial data (safe — skips existing records)
    try:
        from seed import seed
        seed()
    except Exception as e:
        print(f"⚠️  Seed skipped: {e}")

    yield
    print("🔴 Shutting down Makari Islamic TV API")


app = FastAPI(
    title="Makari Islamic TV API",
    description="Complete Islamic streaming platform for Malam Ibrahim Makari",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow all origins (tighten in production if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Media file serving
for folder in ["media/videos", "media/audio", "media/images", "media/pdfs", "media/thumbnails"]:
    os.makedirs(folder, exist_ok=True)

app.mount("/media", StaticFiles(directory="media"), name="media")

# ── Routers ──────────────────────────────────────────────────
app.include_router(auth.router,          prefix="/api/auth",          tags=["Authentication"])
app.include_router(users.router,         prefix="/api/users",         tags=["Users"])
app.include_router(lectures.router,      prefix="/api/lectures",      tags=["Lectures"])
app.include_router(videos.router,        prefix="/api/videos",        tags=["Videos"])
app.include_router(audio.router,         prefix="/api/audio",         tags=["Audio"])
app.include_router(categories.router,    prefix="/api/categories",    tags=["Categories"])
app.include_router(search.router,        prefix="/api/search",        tags=["Search"])
app.include_router(favorites.router,     prefix="/api/favorites",     tags=["Favorites"])
app.include_router(downloads.router,     prefix="/api/downloads",     tags=["Downloads"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(library.router,       prefix="/api/library",       tags=["Library"])
app.include_router(live.router,          prefix="/api/live",          tags=["Live Streaming"])
app.include_router(admin.router,         prefix="/api/admin",         tags=["Admin"])
app.include_router(prayer.router,        prefix="/api/prayer",        tags=["Prayer Times"])


@app.get("/", tags=["Status"])
async def root():
    return {
        "app": "Makari Islamic TV",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Status"])
async def health():
    from database import check_db_connection
    db_ok = check_db_connection()
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "error",
    }
