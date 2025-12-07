import os
import io
import sys
import json
import gzip
import glob
import httpx
import shutil
import zipfile
import datetime
import concurrent.futures
from pathlib import Path
from collections import Counter
from checksumdir import dirhash
from urllib.parse import quote_plus
from rich.progress import Progress, BarColumn
from typing import Any
from . import APIAuth, __version__
from .BaseProvider import BaseAddon, BaseAddonProvider
from .WagoAddons import parse_wagoapp_payload, parse_wagoaddons_error
from .ModManager import CLEAN_BACKUP_DIR, ModManager
from .CurseForge import CurseForgeProvider
from .WagoAddons import WagoAddonsProvider
from .WoWInterface import WoWInterfaceProvider
from .GitHub import GitHubProvider
from .Tukui import TukuiProvider
from .CustomRepository import CustomRepositoryProvider


class Core:
    def __init__(self) -> None:
        self.http: httpx.Client = httpx.Client(
            headers={'User-Agent': f'CurseBreaker/{__version__}'},
            timeout=10,
            http2=True,
            follow_redirects=True
        )
        self.path: Path = Path('Interface/AddOns')
        self.configPath: Path = Path('WTF/CurseBreaker.json')
        self.clientType: str | None = None
        self.config: dict[str, Any] = None
        self.masterConfig: dict[str, Any] = None
        self.dirIndex: dict[str, Any] | None = None
        self.checksumCache: dict[str, bool] = {}
        self.mod_manager: ModManager = ModManager(self)
        self.providers: list[BaseAddonProvider] = []

    def init_master_config(self) -> None:
        self.masterConfig = {'CustomRepository': {}, 'ClientTypes': {}}
        if 'CURSEBREAKER_OFFLINE' in os.environ:
            return
        try:
            self.masterConfig = json.load(gzip.open(io.BytesIO(
                self.http.get('https://cursebreaker.acidweb.dev/config-v2.json.gz').content)))
        except (StopIteration, UnicodeDecodeError, json.JSONDecodeError, httpx.RequestError) as e:
            raise RuntimeError('Failed to fetch the master config file. '
                               'Check your connectivity to Google Cloud.') from e

    def init_config(self) -> None:
        if os.path.isfile('CurseBreaker.json'):
            shutil.move('CurseBreaker.json', 'WTF')
        if os.path.isfile(Path('WTF/CurseBreaker.cache')):
            os.remove(Path('WTF/CurseBreaker.cache'))
        if os.path.isfile(self.configPath):
            with open(self.configPath) as f:
                try:
                    self.config = json.load(f)
                except (StopIteration, UnicodeDecodeError, json.JSONDecodeError) as e:
                    raise RuntimeError from e
        else:
            self.config = {'Addons': [],
                           'WAStash': [],
                           'IgnoreClientVersion': {},
                           'Backup': {'Enabled': True, 'Number': 7},
                           'Version': __version__,
                           'WAUsername': '',
                           'WAAccountName': '',
                           'WAAPIKey': '',
                           'GHAPIKey': '',
                           'WAAAPIKey': '',
                           'CBCompanionVersion': 0,
                           'CompactMode': False,
                           'AutoUpdate': True,
                           'ShowAuthors': True,
                           'ShowSources': False,
                           'AutoUpdateDelay': True,
                           'Mods': {}}
            self.save_config()
        if not os.path.isdir('WTF-Backup') and self.config['Backup']['Enabled']:
            os.mkdir('WTF-Backup')
        self.update_config()

    def save_config(self) -> None:
        with open(self.configPath, 'w') as outfile:
            json.dump(self.config, outfile, sort_keys=True, indent=4, separators=(',', ': '))

    def update_config(self) -> None:
        if 'Version' in self.config.keys() and self.config['Version'] == __version__:
            return
        urlupdate = {'elvui-classic': 'elvui', 'elvui-classic:dev': 'elvui:dev', 'tukui-classic': 'tukui',
                     'sle:dev': 'shadow&light:dev', 'elvui:beta': 'elvui:dev'}
        # 4.0.0
        if 'WACompanionVersion' in self.config and os.path.isdir(Path('Interface/AddOns/WeakAurasCompanion')):
            shutil.rmtree(Path('Interface/AddOns/WeakAurasCompanion'), ignore_errors=True)
        for addon in self.config['Addons']:
            # 1.1.0
            if 'Checksums' not in addon.keys():
                checksums = {}
                for directory in addon['Directories']:
                    checksums[directory] = dirhash(self.path / directory)
                addon['Checksums'] = checksums
            # 1.1.1
            if addon['Version'] is None:
                addon['Version'] = '1'
            # 2.2.0, 3.9.4, 3.12.0
            if addon['URL'].lower() in urlupdate:
                addon['URL'] = urlupdate[addon['URL'].lower()]
            # 2.4.0
            if addon['Name'] == 'TukUI':
                addon['Name'] = 'Tukui'
                addon['URL'] = 'Tukui'
            # 2.7.3
            addon['Directories'] = list(filter(None, set(addon['Directories'])))
            # 3.0.2
            if addon['URL'].endswith('/'):
                addon['URL'] = addon['URL'][:-1]
            # 3.3.0
            if 'Development' in addon.keys() and isinstance(addon['Development'], bool):
                addon['Development'] = 1
            # 4.3.0
            if addon['URL'].startswith('https://www.tukui.org/classic-tbc-addons.php?id='):
                addon['URL'] = addon['URL'].replace('https://www.tukui.org/classic-tbc-addons.php?id=',
                                                    'https://www.tukui.org/classic-wotlk-addons.php?id=')
            # Dependency tracking
            if 'ProviderId' not in addon:
                addon['ProviderId'] = None
            if 'RequiredBy' not in addon:
                addon['RequiredBy'] = []
            if 'InstalledAsDependency' not in addon:
                addon['InstalledAsDependency'] = False
        for add in [['2.1.0', 'WAUsername', ''],
                    ['2.2.0', 'WAAccountName', ''],
                    ['2.2.0', 'WAAPIKey', ''],
                    ['2.2.0', 'WACompanionVersion', 0],
                    ['2.8.0', 'IgnoreClientVersion', {}],
                    ['3.0.1', 'CFCacheTimestamp', 0],
                    ['3.1.10', 'CFCacheCloudFlare', {}],
                    ['3.7.0', 'CompactMode', False],
                    ['3.10.0', 'AutoUpdate', True],
                    ['3.12.0', 'ShowAuthors', True],
                    ['3.16.0', 'IgnoreDependencies', {}],
                    ['3.18.0', 'WAStash', []],
                    ['3.20.0', 'GHAPIKey', ''],
                    ['4.0.0', 'WAAAPIKey', ''],
                    ['4.0.0', 'CBCompanionVersion', 0],
                    ['4.2.0', 'ShowSources', False],
                    ['4.7.0', 'AutoUpdateDelay', True],
                    ['5.0.0', 'Mods', {}]]:
            if add[1] not in self.config.keys():
                self.config[add[1]] = add[2]
        for delete in [['1.3.0', 'URLCache'],
                       ['3.0.1', 'CurseCache'],
                       ['4.0.0', 'CFCacheCloudFlare'],
                       ['4.0.0', 'CFCacheTimestamp'],
                       ['4.0.0', 'IgnoreDependencies'],
                       ['4.0.0', 'WACompanionVersion']]:
            if delete[1] in self.config.keys():
                self.config.pop(delete[1], None)
        self.config['Version'] = __version__
        self.save_config()

    def init_providers(self) -> None:
        """Initialize providers after config and master config are loaded."""
        self.providers = [
            CurseForgeProvider(self.http, self.config, self.masterConfig),
            WagoAddonsProvider(self.http, self.config, self.masterConfig),
            WoWInterfaceProvider(self.http, self.config, self.masterConfig),
            GitHubProvider(self.http, self.config, self.masterConfig),
            TukuiProvider(self.http, self.config, self.masterConfig),
            CustomRepositoryProvider(self.http, self.config, self.masterConfig),
        ]

    def check_if_installed(self, url: str) -> dict[str, Any] | None:
        for addon in self.config['Addons']:
            if url in (addon['URL'], addon['Name']):
                return addon
        return None

    def check_if_installed_dirs(self, directories: list[str]) -> dict[str, Any] | None:
        for addon in self.config['Addons']:
            if Counter(directories) == Counter(addon['Directories']):
                return addon
        return None

    def check_if_installed_by_provider_id(self, provider_id: int | str) -> dict[str, Any] | None:
        """Check if an addon is installed by its provider-specific ID."""
        for addon in self.config['Addons']:
            if addon.get('ProviderId') == provider_id:
                return addon
        return None

    def check_if_dev(self, url: str) -> int:
        if addon := self.check_if_installed(url):
            return addon['Development'] if 'Development' in addon.keys() else 0
        else:
            return 0

    def check_if_overlap(self) -> str | bool:
        directories: list[str] = []
        found: set[str] = set()
        for addon in self.config['Addons']:
            directories = directories + addon['Directories']
        if dupes := [x for x in directories if x in found or found.add(x)]:
            addons: list[str] = []
            for addon in self.config['Addons']:
                if set(addon['Directories']).intersection(dupes):
                    addons.append(addon['Name'])
            addons.sort()
            return '\n'.join(addons)
        else:
            return False

    def check_if_blocked(self, addon: dict[str, Any] | None) -> bool:
        return bool(addon and 'Block' in addon.keys())

    def check_if_dev_global(self) -> int:
        """Check if any dev-capable addon has dev mode enabled."""
        for addon in self.config['Addons']:
            for provider in self.providers:
                if provider.is_addon_url(addon['URL']) and provider.name in ['Wago', 'CF']:
                    if 'Development' in addon.keys():
                        return addon['Development']
                    break
        return 0

    def check_if_from_gh(self) -> bool:
        if self.config['GHAPIKey'] != '':
            return False
        count = 0
        for addon in self.config['Addons']:
            if addon['URL'].startswith('https://github.com/'):
                count += 1
        return count > 4

    def cleanup(self, directories: list[str]) -> None:
        if len(directories) > 0:
            for directory in directories:
                shutil.rmtree(self.path / directory, ignore_errors=True)

    def _save_clean_zip(self, addon_name: str, version: str, zip_content: bytes) -> None:
        """Save original downloaded ZIP file for mod diffing"""
        clean_dir = Path(f'{CLEAN_BACKUP_DIR}/{addon_name}')
        clean_dir.mkdir(parents=True, exist_ok=True)

        zip_path = clean_dir / f'{version}.zip'
        with open(zip_path, 'wb') as f:
            f.write(zip_content)

    def _cleanup_old_clean_zips(self, addon_name: str) -> None:
        """Remove old clean ZIPs that are no longer needed"""
        clean_dir = Path(f'{CLEAN_BACKUP_DIR}/{addon_name}')
        if not clean_dir.exists():
            return

        # Get all ZIPs
        zips = list(clean_dir.glob('*.zip'))
        if len(zips) <= 1:
            return

        # Get versions referenced by mods
        referenced_versions: set[str] = set()
        if addon_name in self.config.get('Mods', {}):
            for mod_data in self.config['Mods'][addon_name].values():
                if 'baseVersion' in mod_data:
                    referenced_versions.add(f"{mod_data['baseVersion']}.zip")

        # Get current addon version
        addon = self.check_if_installed(addon_name)
        if addon:
            referenced_versions.add(f"{addon['Version']}.zip")

        # Remove unreferenced ZIPs
        for zip_file in zips:
            if zip_file.name not in referenced_versions:
                zip_file.unlink()

        # Remove directory if empty
        if not list(clean_dir.glob('*')):
            clean_dir.rmdir()

    def parse_url(self, url: str) -> BaseAddon:
        # Check for legacy/unsupported providers
        if url.startswith('https://www.townlong-yak.com/addons/'):
            raise RuntimeError(f'{url}\nTownlong Yak is no longer supported by this application.')
        if url.startswith('https://www.tukui.org/'):
            raise RuntimeError(f'{url}\nTukui.org is no longer supported by this application.')

        # Ensure clientType is set
        if not self.clientType:
            raise RuntimeError('Client type is not initialized.')

        # Find matching provider
        for provider in self.providers:
            client_type = 'retail' if url in self.config['IgnoreClientVersion'].keys() else self.clientType
            client_version = self.masterConfig['ClientTypes'].get(self.clientType, {}).get('CurrentVersion')

            if provider.is_addon_url(url):
                return provider.create_addon(
                    url, client_type, client_version=client_version, dev_level=self.check_if_dev(url)
                )

        raise NotImplementedError('Provided URL is not supported.')

    def parse_url_source(self, url: str) -> tuple[str, str | None]:
        for provider in self.providers:
            if provider.is_addon_url(url):
                return provider.name, provider.get_website_url(url)
        return '?', None

    def _resolve_dependencies(
        self, url: str, visited: set[Any] | None = None
    ) -> tuple[list[tuple[str, str, Any]], set[Any]]:
        """
        Recursively resolve all required dependencies for an addon.
        Returns (dependencies_list, all_provider_ids).
        Raises RuntimeError on circular dependencies or missing deps.

        This method is provider-agnostic - it delegates to the provider's get_dependencies() method.
        """
        if visited is None:
            visited = set()

        # Identify which provider handles this URL
        provider = None
        for p in self.providers:
            if p.is_addon_url(url):
                provider = p
                break

        if not provider:
            return [], set()

        # Get addon instance to extract provider ID
        addon = self.parse_url(url)

        # Check if provider ID exists (None for providers without IDs)
        if addon.providerId is None:
            return [], set()

        # Detect circular dependencies
        if addon.providerId in visited:
            raise RuntimeError(f'{addon.name}.\nCircular dependency detected.')

        visited.add(addon.providerId)
        all_dependencies: list[tuple[str, str, Any]] = []
        all_provider_ids = {addon.providerId}

        # Get dependencies from provider (returns empty list if not supported)
        dependencies = provider.get_dependencies(url)

        # Process each dependency
        for dep_url, dep_name, dep_provider_id in dependencies:
            # Skip if already installed
            if self.check_if_installed_by_provider_id(dep_provider_id):
                all_provider_ids.add(dep_provider_id)
                continue

            # Recursively resolve nested dependencies
            try:
                nested_deps, nested_ids = self._resolve_dependencies(dep_url, visited.copy())

                # Add nested deps first (install order), then this dep
                all_dependencies.extend(nested_deps)
                all_dependencies.append((dep_url, dep_name, dep_provider_id))
                all_provider_ids.add(dep_provider_id)
                all_provider_ids.update(nested_ids)
            except Exception as e:
                raise RuntimeError(f'{addon.name}.\nFailed to resolve dependency: {e!s}') from e

        return all_dependencies, all_provider_ids

    def parse_new_addon(self, ignore: bool, url: str) -> tuple[bool, str, str]:
        if ignore:
            self.config['IgnoreClientVersion'][url] = True
        new = self.parse_url(url)
        new.get_addon()
        if addon := self.check_if_installed_dirs(new.directories):
            return False, addon['Name'], addon['Version']
        self.cleanup(new.directories)
        # Save original ZIP before installing
        if new.zipContent is not None:
            self._save_clean_zip(new.name, new.currentVersion, new.zipContent)
        new.install(self.path)
        checksums = {}
        for directory in new.directories:
            checksums[directory] = dirhash(self.path / directory)
        self.config['Addons'].append({'Name': new.name,
                                      'URL': url,
                                      'Version': new.currentVersion,
                                      'Directories': new.directories,
                                      'Checksums': checksums,
                                      'ProviderId': new.providerId,
                                      'RequiredBy': [],
                                      'InstalledAsDependency': False})
        self.save_config()
        return True, new.name, new.currentVersion or ""

    def add_addon_with_dependencies(
        self, url: str, ignore: bool, confirm_callback: Any = None
    ) -> tuple[bool, str, str, list[str]]:
        """
        Add addon with dependency resolution, confirmation, and rollback.
        Returns (installed, name, version, installed_dependencies).
        """
        # Normalize URL (same logic as add_addon)
        if url.endswith(':'):
            raise NotImplementedError('Provided URL is not supported.')
        if 'wago-app://' in url:
            url = parse_wagoapp_payload(url, self.clientType, self.config['WAAAPIKey'], self.http)
        for provider in self.providers:
            if provider.prefix and url.startswith(f'{provider.prefix}:'):
                identifier = url[len(provider.prefix)+1:]
                url = provider.convert_id_to_url(identifier)
                break
        if url.endswith('/'):
            url = url[:-1]

        # Check if already installed
        if addon := self.check_if_installed(url):
            return False, addon['Name'], addon['Version'], []

        # Resolve dependencies
        dependencies_to_install, _ = self._resolve_dependencies(url)

        # Get user confirmation if callback provided
        if dependencies_to_install and confirm_callback:
            if not confirm_callback(dependencies_to_install):
                raise RuntimeError('Installation cancelled by user.')

        # Install dependencies first, with rollback on failure
        installed_dep_names: list[str] = []
        installed_dep_urls: list[str] = []
        rollback_needed = False

        try:
            # Install each dependency
            for dep_url, dep_name, dep_provider_id in dependencies_to_install:
                try:
                    installed, name, version = self.parse_new_addon(ignore, dep_url)
                    if installed:
                        # Mark as dependency and add metadata
                        addon_record = self.check_if_installed(dep_url)
                        if addon_record:
                            addon_record['ProviderId'] = dep_provider_id
                            addon_record['InstalledAsDependency'] = True
                            addon_record['RequiredBy'] = [url]
                            self.save_config()
                            installed_dep_names.append(name)
                            installed_dep_urls.append(dep_url)
                except Exception as e:
                    rollback_needed = True
                    raise RuntimeError(f'Failed to install dependency {dep_name}: {e!s}') from e

            # Install main addon
            try:
                installed, name, version = self.parse_new_addon(ignore, url)

                if installed:
                    addon_record = self.check_if_installed(url)
                    if addon_record:
                        # Get providerId from addon instance
                        addon_instance = self.parse_url(url)
                        addon_record['ProviderId'] = addon_instance.providerId
                        addon_record['InstalledAsDependency'] = False
                        addon_record['RequiredBy'] = []
                        self.save_config()

                        # Update RequiredBy for dependencies
                        for dep_url, _, _ in dependencies_to_install:
                            if dep_addon := self.check_if_installed(dep_url):
                                if url not in dep_addon['RequiredBy']:
                                    dep_addon['RequiredBy'].append(url)
                        self.save_config()

            except Exception as e:
                rollback_needed = True
                raise RuntimeError(f'Failed to install {url}: {e!s}') from e
            else:
                return installed, name, version, installed_dep_names
        finally:
            # Rollback on failure
            if rollback_needed:
                for dep_url in installed_dep_urls:
                    try:
                        self.del_addon(dep_url, keep=False)
                    except Exception:
                        pass  # Best effort rollback

    def del_addon(self, url: str, keep: bool, force: bool = False) -> tuple[str | None, str | None, list[str]]:
        """Delete addon with dependency checking. Returns (name, version, removed_dependencies)."""
        if old := self.check_if_installed(url):
            # Check if required by other addons
            if not force and old.get('RequiredBy'):
                required_by_names: list[str] = []
                for dep_url in old['RequiredBy']:
                    if dep_addon := self.check_if_installed(dep_url):
                        required_by_names.append(dep_addon['Name'])

                if required_by_names:
                    raise RuntimeError(
                        f'{old["Name"]} is required by: {", ".join(required_by_names)}.\n'
                        f'Remove those addons first, or use force delete.'
                    )

            # Clean up RequiredBy references in all addons
            # Find all addons that this addon depends on and remove this URL from their RequiredBy lists
            orphaned_deps: list[dict[str, Any]] = []
            for addon in self.config['Addons']:
                if old['URL'] in addon.get('RequiredBy', []):
                    addon['RequiredBy'].remove(old['URL'])
                    # Track dependencies that are now orphaned (auto-installed and no longer required)
                    if addon.get('InstalledAsDependency') and not addon['RequiredBy']:
                        orphaned_deps.append(addon)

            # Original deletion logic
            if not keep:
                self.cleanup(old['Directories'])
            self.config['IgnoreClientVersion'].pop(old['URL'], None)
            self.config['Addons'][:] = [d for d in self.config['Addons'] if d.get('URL') != url
                                        and d.get('Name') != url]

            # Cleanup clean ZIPs if no mods exist
            if old['Name'] not in self.config.get('Mods', {}):
                clean_dir = Path(f'{CLEAN_BACKUP_DIR}/{old["Name"]}')
                if clean_dir.exists():
                    shutil.rmtree(clean_dir)

            self.save_config()

            # Auto-remove orphaned dependencies and collect their names
            removed_deps: list[str] = []
            for dep in orphaned_deps:
                dep_name, _, nested_deps = self.del_addon(dep['URL'], keep=False, force=True)
                if dep_name:
                    removed_deps.append(dep_name)
                    removed_deps.extend(nested_deps)

            return old['Name'], old['Version'], removed_deps
        return None, None, []

    def update_addon(
        self, url: str, update: bool, force: bool
    ) -> tuple[str, list[str], str | None, str | None, str | None, bool, bool, str, str | None, str | None, int | None]:
        if not (old := self.check_if_installed(url)):
            return url, [], None, None, None, False, False, '?', None, None, None
        dev = self.check_if_dev(old['URL'])
        blocked = self.check_if_blocked(old)
        oldversion = old['Version']
        modified = self.checksumCache[old['URL']] if old['URL'] in self.checksumCache else self.check_checksum(old)[1]
        if old['URL'].startswith(('https://www.townlong-yak.com/addons/',
                                  'https://www.tukui.org/')):
            return old['Name'], [], oldversion, oldversion, None, modified, blocked, 'Unsupported', old['URL'], \
                       None, dev
        source, sourceurl = self.parse_url_source(old['URL'])
        new = self.parse_url(old['URL'])
        if force or (new.currentVersion != old['Version'] and update and not modified and not blocked):
            new.get_addon()
            # Save original ZIP before installing
            if new.zipContent is not None:
                self._save_clean_zip(new.name, new.currentVersion, new.zipContent)
            self.cleanup(old['Directories'])
            new.install(self.path)

            # Reapply mods if they exist
            if new.name in self.config.get('Mods', {}):
                try:
                    self.mod_manager.reapply_all_mods(new.name)

                    # Recalculate checksums after mod application
                    checksums = {}
                    for directory in new.directories:
                        checksums[directory] = dirhash(self.path / directory)
                except Exception:
                    # If mod application fails, just use clean checksums
                    checksums = {}
                    for directory in new.directories:
                        checksums[directory] = dirhash(self.path / directory)
            else:
                checksums = {}
                for directory in new.directories:
                    checksums[directory] = dirhash(self.path / directory)

            old['Name'] = new.name
            old['Version'] = new.currentVersion
            old['Directories'] = new.directories
            old['Checksums'] = checksums
            old['ProviderId'] = new.providerId
            self.save_config()

            # Cleanup old ZIPs
            self._cleanup_old_clean_zips(new.name)
        if force:
            modified = False
            blocked = False
        return new.name, new.author, new.currentVersion, oldversion, new.uiVersion, modified, blocked, source, \
            sourceurl, new.changelogUrl, dev

    def check_checksum(self, addon: dict[str, Any], pbar: Any = None) -> tuple[str, bool]:
        checksums = {}
        for directory in addon['Directories']:
            if os.path.isdir(self.path / directory):
                checksums[directory] = dirhash(self.path / directory)
        if pbar:
            pbar.update(0, advance=0.5, refresh=True)
        return addon['URL'], len(checksums.items() & addon['Checksums'].items()) != len(addon['Checksums'])

    def bulk_check_checksum(self, addons: list[dict[str, Any]], pbar: Any) -> None:
        self.checksumCache = {}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            workers: list[concurrent.futures.Future[tuple[str, bool]]] = []
            for addon in addons:
                workers.append(executor.submit(self.check_checksum, addon, pbar))
            for future in concurrent.futures.as_completed(workers):
                url, checksums_valid = future.result()
                self.checksumCache[url] = checksums_valid

    def dev_toggle(self, url: str) -> int | None:
        """Toggle development/beta channel for providers that support it."""
        if url == 'global':
            state = self.check_if_dev_global()
            new_state = (state + 1) % 3 or None
            for addon in self.config['Addons']:
                addon['Development'] = new_state
            self.save_config()
            return state

        addon = self.check_if_installed(url)
        if not addon:
            return None
        state = self.check_if_dev(url)
        new_state = (state + 1) % 3 or None
        addon['Development'] = new_state
        self.save_config()
        return state

    def block_toggle(self, url: str) -> bool | None:
        if addon := self.check_if_installed(url):
            state = self.check_if_blocked(addon)
            if state:
                addon.pop('Block', None)
            else:
                addon['Block'] = True
            self.save_config()
            return not state
        return None

    def backup_check(self) -> bool:
        if not self.config['Backup']['Enabled']:
            return False
        if os.path.isfile(Path('WTF-Backup', f'{datetime.datetime.now().strftime("%d%m%y")}.zip')):
            return False
        listofbackups = [Path(x) for x in glob.glob('WTF-Backup/*.zip')]
        if len(listofbackups) >= self.config['Backup']['Number']:
            oldest_file = min(listofbackups, key=os.path.getctime)
            os.remove(oldest_file)
        return True

    def backup_wtf(self, console: Any) -> None:
        archive = Path('WTF-Backup', f'{datetime.datetime.now().strftime("%d%m%y")}.zip')
        if os.path.isfile(archive):
            suffix = 1
            while True:
                archive = Path('WTF-Backup', f'{datetime.datetime.now().strftime("%d%m%y")}-{suffix}.zip')
                if not os.path.isfile(archive):
                    break
                suffix += 1
        zipf = zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED)
        filecount = 0
        for _, _, files in os.walk('WTF/', topdown=True, followlinks=True):
            files = [f for f in files if f[0] != '.']
            filecount += len(files)
        if filecount > 0:
            with Progress('{task.completed}/{task.total}', '|', BarColumn(bar_width=None), '|', auto_refresh=False,
                          console=console) as progress:
                task = progress.add_task('', total=filecount)
                while not progress.finished:
                    for root, _, files in os.walk('WTF/', topdown=True, followlinks=True):
                        files = [f for f in files if f[0] != '.']
                        for f in files:
                            zipf.write(Path(root, f))
                            progress.update(task, advance=1, refresh=True)
        zipf.close()

    def find_orphans(self) -> tuple[list[str], list[str]]:
        orphanedaddon: list[str] = []
        orphaneconfig: list[str] = []
        directories: list[str] = []
        directoriesspecial: list[str] = []
        ignored = ['.DS_Store', '.git']
        special = ['+Wowhead_Looter', 'CurseBreakerCompanion', 'SharedMedia_MyMedia', 'TradeSkillMaster_AppHelper']
        for addon in self.config['Addons']:
            for directory in addon['Directories']:
                directories.append(directory)
        for directory in os.listdir(self.path):
            if os.path.isdir(self.path / directory) and directory not in directories:
                if os.path.isdir(self.path / directory / '.git'):
                    orphanedaddon.append(f'{directory} [GIT]')
                    directoriesspecial.append(directory)
                elif directory in special:
                    orphanedaddon.append(f'{directory} [Special]')
                    directoriesspecial.append(directory)
                elif directory not in ignored:
                    orphanedaddon.append(directory)
        directories += directoriesspecial + orphanedaddon
        for root, _, files in os.walk('WTF/', followlinks=True):
            for f in files:
                if 'Blizzard_' not in f and f.endswith('.lua'):
                    name = os.path.splitext(f)[0]
                    if name not in directories:
                        orphaneconfig.append(str(Path(root, f))[4:])
        return orphanedaddon, orphaneconfig

    def search(self, query: str) -> list[str]:
        if self.config['WAAAPIKey'] == '':
            raise RuntimeError('This feature only searches the database of the Wago Addons. '
                               'So their API key is required.\n'
                               'It can be obtained here: https://addons.wago.io/patreon')
        payload = self.http.get(f'https://addons.wago.io/api/external/addons/_search?query={quote_plus(query.strip())}&'
                                f'game_version={self.clientType}', auth=APIAuth('Bearer', self.config['WAAAPIKey']))
        parse_wagoaddons_error(payload.status_code)
        payload = payload.json()
        return [result['website_url'] for result in payload['data']]

    def create_reg(self) -> None:
        with open('CurseBreaker.reg', 'w') as outfile:
            outfile.write('Windows Registry Editor Version 5.00\n\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\wago-app]\n'
                          '"URL Protocol"="\\"\\""\n'
                          '@="\\"URL:CurseBreaker Protocol\\""\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\wago-app\\DefaultIcon]\n'
                          '@="\\"CurseBreaker.exe,1\\""\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\wago-app\\shell]\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\wago-app\\shell\\open]\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\wago-app\\shell\\open\\command]\n'
                          '@="\\"' + os.path.abspath(sys.executable).replace('\\', '\\\\') + '\\" \\"%1\\""\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\weakauras-companion]\n'
                          '"URL Protocol"="\\"\\""\n'
                          '@="\\"URL:CurseBreaker Protocol\\""\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\weakauras-companion\\DefaultIcon]\n'
                          '@="\\"CurseBreaker.exe,1\\""\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\weakauras-companion\\shell]\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\weakauras-companion\\shell\\open]\n'
                          '[HKEY_CURRENT_USER\\Software\\Classes\\weakauras-companion\\shell\\open\\command]\n'
                          '@="\\"' + os.path.abspath(sys.executable).replace('\\', '\\\\') + '\\" \\"%1\\""')

    def bulk_check(self, addons: list[dict[str, Any]]) -> None:
        """Bulk check for updates using all providers."""
        if not self.clientType:
            return

        for provider in self.providers:
            # Filter addons for this provider (excluding ignored client versions for some providers)
            provider_urls: list[str] = []
            for addon in addons:
                if provider.is_addon_url(addon['URL']):
                    # Skip client version check for providers that don't need it
                    if provider.name in ['Wago', 'CF'] and addon['URL'] in self.config['IgnoreClientVersion'].keys():
                        continue
                    provider_urls.append(addon['URL'])

            if provider_urls:
                provider.bulk_check(provider_urls, self.clientType)

    def detect_accounts(self) -> list[str]:
        if not os.path.isdir(Path('WTF/Account')):
            return []
        accounts = os.listdir(Path('WTF/Account'))
        accounts_processed: list[str] = []
        for account in accounts:
            if os.path.isfile(Path(f'WTF/Account/{account}/SavedVariables/WeakAuras.lua')) or \
                        os.path.isfile(Path(f'WTF/Account/{account}/SavedVariables/Plater.lua')):
                accounts_processed.append(account)
        return accounts_processed

    def detect_addons(self) -> tuple[list[str], list[str], list[str]]:
        """Detect addons using all providers."""
        names: list[str] = []
        namesinstalled: list[str] = []
        slugs: list[str] = []

        if not self.clientType:
            return names, slugs, namesinstalled

        # List of directories to ignore
        ignored = ['ElvUI_OptionsUI', 'ElvUI_Options', 'ElvUI_Libraries', 'Tukui_Config', '+Wowhead_Looter',
                   'WeakAurasCompanion', 'CurseBreakerCompanion', 'SharedMedia_MyMedia', 'TradeSkillMaster_AppHelper',
                   'WagoAnalytics', 'WagoAppCompanion', '.DS_Store', '.git']

        # Collect addon directories
        addon_dirs: list[str] = []
        for directory in os.listdir(self.path):
            if (os.path.isdir(self.path / directory) and
                not os.path.islink(self.path / directory) and
                not os.path.isdir(self.path / directory / '.git') and
                not directory.startswith('Blizzard_') and
                directory not in ignored):
                addon_dirs.append(directory)

        matched_dirs: set[str] = set()

        # Scan with each provider (in priority order)
        for provider in self.providers:
            # Only scan directories not yet matched
            remaining_dirs = [d for d in addon_dirs if d not in matched_dirs]
            if not remaining_dirs:
                break

            detected = provider.scan(remaining_dirs, self.path, self.clientType)

            for addon in detected:
                matched_dirs.update(addon.directories)

                # Check if already installed
                if self.check_if_installed(addon.url):
                    namesinstalled.append(addon.name)
                else:
                    names.append(addon.name)
                    slugs.append(addon.url)

        names.sort()
        namesinstalled.sort()
        slugs.sort()

        return names, slugs, namesinstalled

    def export_addons(self) -> str:
        """Export installed addons to shorthand format."""
        addons: list[str] = []
        for addon in self.config['Addons']:
            url = addon['URL']

            # Find provider and convert to shorthand
            for provider in self.providers:
                if provider.is_addon_url(url):
                    url = provider.url_to_shorthand(url)
                    break

            addons.append(url)

        return f'install {",".join(sorted(addons))}'
