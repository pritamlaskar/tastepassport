from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from database import init_db, check_db_connection
from api.routes import city, places, search, feedback, admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TastePassport API starting — initializing database")
    init_db()
    logger.info("Database initialized")
    yield
    logger.info("TastePassport API shutting down")


app = FastAPI(
    title="TastePassport API",
    description="Human experience index for food discovery. No algorithms. No ads. No SEO.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(city.router, prefix="/api")
app.include_router(places.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(feedback.router, prefix="/api")
app.include_router(admin.router)  # prefix already set in admin.router (/api/admin)


@app.get("/")
def root():
    return {
        "name": "TastePassport API",
        "version": "1.0.0",
        "status": "ok",
        "description": "Human experience index for food discovery",
    }


@app.get("/health")
def health():
    db_ok = check_db_connection()
    redis_ok = _check_redis()

    status = "ok" if db_ok and redis_ok else "degraded"
    return {
        "status": status,
        "database": "ok" if db_ok else "unreachable",
        "redis": "ok" if redis_ok else "unreachable",
    }


def _check_redis() -> bool:
    try:
        import redis as redis_lib
        from config import get_settings
        r = redis_lib.from_url(get_settings().redis_url, socket_connect_timeout=2)
        r.ping()
        return True
    except Exception:
        return False
