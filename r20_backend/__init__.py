"""R20 standalone backend package."""
from .config import settings
from .version import __version__, APP_VERSION, APP_NAME, APP_NAME_EN

__all__ = ["settings", "__version__", "APP_VERSION", "APP_NAME", "APP_NAME_EN"]
