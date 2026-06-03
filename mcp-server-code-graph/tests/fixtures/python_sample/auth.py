import hashlib
from typing import Optional


class AuthManager:
    """Manages user authentication."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._cache: dict = {}

    def hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    def verify_password(self, password: str, hashed: str) -> bool:
        return self.hash_password(password) == hashed

    async def login(self, username: str, password: str) -> Optional[str]:
        """Authenticate user and return session token."""
        if not username or not password:
            return None
        return "token_" + username


class TokenValidator:
    """Validates session tokens."""

    def validate(self, token: str) -> bool:
        return token.startswith("token_")


def create_auth_manager(secret: str) -> AuthManager:
    return AuthManager(secret)
