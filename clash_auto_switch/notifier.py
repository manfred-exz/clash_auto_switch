from plyer import notification


def notify_user(title: str, message: str) -> bool:
    """Send a best-effort cross-platform desktop notification."""
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="Clash Auto Switch",
            timeout=5,
        )
        return True
    except Exception:
        return False
