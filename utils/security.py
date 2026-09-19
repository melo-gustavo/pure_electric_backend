import bcrypt


class SecurityUtils:
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt with a random salt (72-byte limit)."""
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        """Verify a password against a bcrypt hash from ``hash_password``."""
        return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
