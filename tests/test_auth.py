import os
import unittest

# Configure required environment before importing app modules
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-testing-32chars!")

from auth_service import (
    _hash_password,
    _verify_password,
    create_access_token,
    _decode_token,
    register,
    UserCreate,
)
from db import get_user_by_email, get_db_connection


class TestAuthService(unittest.TestCase):

    def setUp(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM token_blacklist")
            conn.commit()

    def test_bcrypt_hash_and_verify(self):
        password = "SecurePassword123!"
        hashed = _hash_password(password)

        self.assertNotEqual(password, hashed)
        self.assertTrue(hashed.startswith("$2b$"))
        self.assertTrue(_verify_password(password, hashed))
        self.assertFalse(_verify_password("WrongPassword", hashed))

    def test_jwt_create_and_decode(self):
        token = create_access_token("user_123")

        decoded = _decode_token(token)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["sub"], "user_123")
        self.assertEqual(decoded["type"], "access")

    def test_jwt_tampered_token_fails(self):
        token = create_access_token("user_123")
        tampered = token[:-5] + "aaaaa"
        with self.assertRaises(Exception):
            _decode_token(tampered)

    def test_user_registration_flow(self):
        user_in = UserCreate(
            email="shopper@example.com",
            password="StrongPassword123",
            full_name="Book Lover",
        )
        created = register(user_in)

        self.assertEqual(created.email, "shopper@example.com")
        self.assertEqual(created.full_name, "Book Lover")
        self.assertTrue(created.is_active)
        db_user = get_user_by_email("shopper@example.com")
        self.assertIsNotNone(db_user)
        self.assertEqual(db_user["email"], "shopper@example.com")


if __name__ == "__main__":
    unittest.main()
