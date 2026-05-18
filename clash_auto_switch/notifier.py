from plyer import notification


APP_NAME = "Clash Auto Switch"


def notify_user(title: str, message: str) -> bool:
    """Send a best-effort cross-platform desktop notification."""
    try:
        notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=5,
        )
        return True
    except Exception:
        return False
