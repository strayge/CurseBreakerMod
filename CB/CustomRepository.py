from typing import Any
import httpx
from .BaseProvider import BaseAddon, BaseAddonProvider
from .GitHub import GitHubAddonRaw


class CustomRepositoryProvider(BaseAddonProvider):
    """Provider for custom repositories defined in masterConfig."""

    def __init__(self, http: httpx.Client, config: dict[str, Any], master_config: dict[str, Any]):
        super().__init__(http, config, master_config)
        self.name: str = "GitHub"  # Uses GitHub for source display
        self.prefix: str = ""

    def is_addon_url(self, url: str) -> bool:
        """Check if URL is a custom repository keyword."""
        return url.lower() in self.masterConfig.get('CustomRepository', {}).keys()

    def convert_url_to_id(self, url: str) -> str:
        """Return the URL as-is (already a keyword)."""
        return url.lower()

    def convert_id_to_url(self, identifier: str) -> str:
        """Return identifier as-is (keywords like 'elvui:dev')."""
        return identifier.lower()

    def get_website_url(self, url: str) -> str | None:
        """Build GitHub URL from repository info."""
        repo_info = self.masterConfig.get('CustomRepository', {}).get(url.lower())
        if repo_info and 'Repository' in repo_info:
            return f'https://github.com/{repo_info["Repository"]}'
        return None

    def url_to_shorthand(self, url: str) -> str:
        """No transformation needed - already using keywords."""
        return url.lower()

    def create_addon(self, url: str, client_type: str, **kwargs: Any) -> BaseAddon:
        """Create GitHubAddonRaw instance from custom repository."""
        repo_info = self.masterConfig.get('CustomRepository', {}).get(url.lower())
        if not repo_info:
            raise RuntimeError(f'{url}\nCustom repository not found in configuration.')

        return GitHubAddonRaw(
            repo_info,
            self.config['GHAPIKey'],
            self.http
        )
