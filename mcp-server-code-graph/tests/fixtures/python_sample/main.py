from auth import create_auth_manager, AuthManager


manager = create_auth_manager("super_secret")


def run(username: str, password: str) -> str:
    result = manager.login(username, password)
    return result


def get_manager() -> AuthManager:
    return manager
