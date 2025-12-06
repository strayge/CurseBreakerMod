from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import zipfile
import httpx


@dataclass
class DetectedAddon:
    """Represents an addon detected during filesystem scan."""
    name: str
    url: str
    directories: list[str]
    is_installed: bool


class BaseAddon(ABC):
    """Base class for all addon implementations."""

    def __init__(self):
        self.name: str = ""
        self.currentVersion: str = ""
        self.downloadUrl: str  = ""
        self.changelogUrl: str = ""
        self.uiVersion: str = ""
        self.directories: list[str] = []
        self.author: list[str] = []
        self.zipContent: bytes | None = None
        self.archive: zipfile.ZipFile | None = None

    @abstractmethod
    def get_addon(self) -> None:
        """Download and prepare the addon for installation."""
        ...

    @abstractmethod
    def install(self, path: Path) -> None:
        """Install the addon to the specified path."""
        ...


class BaseAddonProvider(ABC):
    """Base class for addon provider implementations."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]) -> None:
        self.http: httpx.Client = http
        self.config: dict[str, Any] = config
        self.masterConfig: dict[str, Any] = master_config
        self.cache: dict[str, Any] = {}

        self.name: str = ""
        self.prefix: str = ""

    # ===== URL Handling =====

    @abstractmethod
    def is_addon_url(self, url: str) -> bool:
        """Check if the given URL belongs to this provider."""
        ...

    @abstractmethod
    def convert_url_to_id(self, url: str) -> str:
        """Extract the addon identifier from URL (slug, ID, repo path, etc.)."""
        ...

    @abstractmethod
    def convert_id_to_url(self, identifier: str) -> str:
        """Build full URL from identifier."""
        ...

    def normalize_url(self, url: str) -> str:
        """Normalize URL (remove trailing slashes, etc.)."""
        return url.rstrip('/')

    def get_website_url(self, url: str) -> str | None:
        """Get the human-readable website URL (for display). Default: return url."""
        return url

    # ===== Addon Creation =====

    @abstractmethod
    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        """
        Create an addon instance from URL.

        Args:
            url: The addon URL
            client_type: Client type (retail, classic, mop)
            **kwargs: Provider-specific options (dev_level, client_version, etc.)

        Returns:
            BaseAddon instance
        """
        ...

    # ===== Bulk Operations =====

    def bulk_check(self, addon_urls: list[str], client_type: str) -> None:
        """
        Bulk check for updates for multiple addons.
        Populates self.cache with results.

        Args:
            addon_urls: List of addon URLs from this provider
            client_type: Current client type
        """
        # Default: do nothing (providers can override if they support bulk checking)
        _ = addon_urls, client_type
        return

    def scan(self, addon_dirs: list[str], path: Path, client_type: str) -> list[DetectedAddon]:
        """
        Scan local directories to detect addons from this provider.

        Args:
            addon_dirs: List of addon directory names to check
            path: Base path to Interface/AddOns
            client_type: Current client type

        Returns:
            List of DetectedAddon instances
        """
        # Default: return empty list (providers can override if they support scanning)
        _ = addon_dirs, path, client_type
        return []

    # ===== Export =====

    def url_to_shorthand(self, url: str) -> str:
        """
        Convert full URL to shorthand format for export.
        Default implementation uses prefix.

        Args:
            url: Full addon URL

        Returns:
            Shorthand URL (e.g., 'wa:slug' or 'gh:owner/repo')
        """
        if not self.prefix:
            return url
        identifier = self.convert_url_to_id(url)
        return f'{self.prefix}:{identifier}'
