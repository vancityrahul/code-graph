import pytest
from auth import AuthManager, create_auth_manager


def test_hash_password():
    mgr = AuthManager("secret")
    hashed = mgr.hash_password("password")
    assert len(hashed) == 64


def test_verify_password():
    mgr = AuthManager("secret")
    hashed = mgr.hash_password("password")
    assert mgr.verify_password("password", hashed)


def test_verify_password_wrong():
    mgr = AuthManager("secret")
    hashed = mgr.hash_password("password")
    assert not mgr.verify_password("wrong", hashed)


def test_create_auth_manager():
    mgr = create_auth_manager("secret")
    assert mgr.secret == "secret"
