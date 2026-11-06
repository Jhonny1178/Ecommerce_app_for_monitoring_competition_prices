import os

from flask import session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def user_rate_key():
    return f"user:{session.get('user_id')}"


limiter = Limiter(
    get_remote_address,
    default_limits=[],
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
)
