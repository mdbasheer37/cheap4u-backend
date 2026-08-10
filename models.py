from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum

class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"
    moderator = "moderator"

class ContentType(str, enum.Enum):
    video = "video"
    audio = "audio"
    pdf = "pdf"
    article = "article"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    full_name = Column(String(100))
    hashed_password = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.user)
    avatar = Column(String(255))
    language = Column(String(10), default="en")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    favorites = relationship("Favorite", back_populates="user")
    downloads = relationship("Download", back_populates="user")
    notifications = relationship("UserNotification", back_populates="user")
    watch_history = relationship("WatchHistory", back_populates="user")

class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True, index=True)
    name_en = Column(String(100), nullable=False)
    name_ha = Column(String(100))
    slug = Column(String(100), unique=True, index=True)
    description = Column(Text)
    icon = Column(String(50))
    color = Column(String(20))
    thumbnail = Column(String(255))
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    lectures = relationship("Lecture", back_populates="category")

class Lecture(Base):
    __tablename__ = "lectures"
    id = Column(Integer, primary_key=True, index=True)
    title_en = Column(String(255), nullable=False)
    title_ha = Column(String(255))
    description_en = Column(Text)
    description_ha = Column(Text)
    category_id = Column(Integer, ForeignKey("categories.id"))
    thumbnail = Column(String(255))
    duration = Column(Integer)
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    is_featured = Column(Boolean, default=False)
    is_trending = Column(Boolean, default=False)
    is_published = Column(Boolean, default=True)
    tags = Column(Text)
    date_recorded = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    category = relationship("Category", back_populates="lectures")
    videos = relationship("Video", back_populates="lecture")
    audio_files = relationship("AudioFile", back_populates="lecture")
    favorites = relationship("Favorite", back_populates="lecture")
    watch_history = relationship("WatchHistory", back_populates="lecture")

class Video(Base):
    __tablename__ = "videos"
    id = Column(Integer, primary_key=True, index=True)
    lecture_id = Column(Integer, ForeignKey("lectures.id"))
    title = Column(String(255), nullable=False)
    file_path = Column(String(500))
    stream_url = Column(String(500))
    youtube_url = Column(String(500))
    quality = Column(String(20), default="720p")
    file_size = Column(Integer)
    duration = Column(Integer)
    view_count = Column(Integer, default=0)
    is_downloadable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    lecture = relationship("Lecture", back_populates="videos")

class AudioFile(Base):
    __tablename__ = "audio_files"
    id = Column(Integer, primary_key=True, index=True)
    lecture_id = Column(Integer, ForeignKey("lectures.id"))
    title = Column(String(255), nullable=False)
    file_path = Column(String(500))
    stream_url = Column(String(500))
    file_size = Column(Integer)
    duration = Column(Integer)
    bitrate = Column(Integer)
    play_count = Column(Integer, default=0)
    is_downloadable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    lecture = relationship("Lecture", back_populates="audio_files")

class LiveStream(Base):
    __tablename__ = "live_streams"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    stream_url = Column(String(500), nullable=False)
    thumbnail = Column(String(255))
    stream_type = Column(String(50))
    is_live = Column(Boolean, default=False)
    viewer_count = Column(Integer, default=0)
    scheduled_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Book(Base):
    __tablename__ = "books"
    id = Column(Integer, primary_key=True, index=True)
    title_en = Column(String(255), nullable=False)
    title_ha = Column(String(255))
    author = Column(String(100))
    description = Column(Text)
    cover_image = Column(String(255))
    file_path = Column(String(500))
    file_size = Column(Integer)
    page_count = Column(Integer)
    category = Column(String(100))
    language = Column(String(20))
    download_count = Column(Integer, default=0)
    is_downloadable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Favorite(Base):
    __tablename__ = "favorites"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    lecture_id = Column(Integer, ForeignKey("lectures.id"), nullable=True)
    book_id = Column(Integer, ForeignKey("books.id"), nullable=True)
    content_type = Column(Enum(ContentType))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="favorites")
    lecture = relationship("Lecture", back_populates="favorites")

class Download(Base):
    __tablename__ = "downloads"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    content_type = Column(Enum(ContentType))
    content_id = Column(Integer)
    file_path = Column(String(500))
    file_size = Column(Integer)
    progress = Column(Float, default=0.0)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    user = relationship("User", back_populates="downloads")

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    body = Column(Text)
    notification_type = Column(String(50))
    target_url = Column(String(255))
    image = Column(String(255))
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_notifications = relationship("UserNotification", back_populates="notification")

class UserNotification(Base):
    __tablename__ = "user_notifications"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    notification_id = Column(Integer, ForeignKey("notifications.id"))
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="notifications")
    notification = relationship("Notification", back_populates="user_notifications")

class WatchHistory(Base):
    __tablename__ = "watch_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    lecture_id = Column(Integer, ForeignKey("lectures.id"))
    content_type = Column(Enum(ContentType))
    progress = Column(Float, default=0.0)
    duration = Column(Integer)
    last_watched = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="watch_history")
    lecture = relationship("Lecture", back_populates="watch_history")
