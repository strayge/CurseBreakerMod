import os
import io
import re
import httpx
import zipfile
from typing import Any
from pathlib import Path
from . import retry


CF_API_KEY = '$2a$10$bL4bIL5pUWqfcO7KQtnMReakwtfHbNKh6v1uTpKlzhwoueEJQnPnm'
GAME_VERSION_TYPE_MAP = {'retail': 517, 'classic': 67408, 'mop': 79434}


class CurseForgeAddon:
    @retry()
    def __init__(
        self, url: str, checkcache: dict[str, Any], clienttype: str, allowdev: int, http: httpx.Client
    ) -> None:
        slug = url.split('/')[-1]
        self.http: httpx.Client = http
        self.clientType: str = clienttype
        self.allowDev: int = allowdev

        if slug in checkcache:
            self.payload = checkcache[slug]
        else:
            try:
                response = self.http.get(f'https://api.curseforge.com/v1/mods/search?gameId=1&slug={slug}',
                                        headers={'x-api-key': CF_API_KEY}, timeout=15)
            except httpx.RequestError as e:
                raise RuntimeError(f'{url}\nCurseForge API failed to respond.') from e

            try:
                data = response.json()['data']
            except (StopIteration, KeyError) as e:
                raise RuntimeError(f'{url}\nFailed to parse CurseForge API response.') from e

            if not data:
                raise RuntimeError(f'{url}\nAddon not found on CurseForge.')

            self.payload = data[0]

        self.name: str = self.payload['name'].strip().strip('\u200b')
        self.changelogUrl: str = self.payload['links']['websiteUrl']
        self.author: list[str] = [author['name'] for author in self.payload['authors']]
        self.downloadUrl: str | None = None
        self.currentVersion: str | None = None
        self.uiVersion: str | None = None
        self.archive: zipfile.ZipFile | None = None
        self.directories: list[str] = []
        self.zipContent: bytes = b''
        self.get_current_version()

    def get_current_version(self) -> None:
        game_version_type_id = GAME_VERSION_TYPE_MAP.get(self.clientType)
        if not game_version_type_id:
            raise RuntimeError(f'{self.name}.\nUnsupported client type: {self.clientType}')

        # Filter files by game version type and release type
        compatible_files = []
        max_release_type = self.allowDev + 1  # 0->1 (stable), 1->2 (stable+beta), 2->3 (all)

        for file_index in self.payload['latestFilesIndexes']:
            if (file_index['gameVersionTypeId'] == game_version_type_id and
                file_index['releaseType'] <= max_release_type):
                compatible_files.append(file_index)

        if not compatible_files:
            raise RuntimeError(f'{self.name}.\nFailed to find release for your client version.')

        # Select file with highest fileId (most recent)
        selected_file_index = max(compatible_files, key=lambda x: x['fileId'])
        selected_file_id = selected_file_index['fileId']

        # Find the full file data in latestFiles
        selected_file = None
        for file_data in self.payload['latestFiles']:
            if file_data['id'] == selected_file_id:
                selected_file = file_data
                break

        if not selected_file:
            raise RuntimeError(f'{self.name}.\nFailed to find file data for selected release.')

        self.downloadUrl = selected_file['downloadUrl']
        self.currentVersion = selected_file['displayName']
        self.uiVersion = selected_file_index['gameVersion']

    @retry()
    def get_addon(self) -> None:
        self.zipContent = self.http.get(self.downloadUrl).content
        self.archive = zipfile.ZipFile(io.BytesIO(self.zipContent))
        for file in self.archive.namelist():
            if '/' not in os.path.dirname(file):
                self.directories.append(os.path.dirname(file))
        self.directories = list(filter(None, set(self.directories)))
        if not self.directories:
            raise RuntimeError(f'{self.name}.\nProject package is corrupted or incorrectly packaged.')

    def install(self, path: Path) -> None:
        self.archive.extractall(path)


class CurseForgeFingerprintScanner:
    def __init__(self, directory: Path) -> None:
        self.directory: Path = directory
        self.filesToHash: list[Path] = []
        self.filesToParse: list[Path] = []
        self.individualFingerprints: list[int] = []
        self.folderFingerprint: int | None = None
        self.parse()

    def parse_file(self, target: list[Path]) -> None:
        for f in target:
            if f.is_file():
                self.filesToHash.append(f)
                if not f.name.lower().endswith('.lua'):
                    with open(f, encoding='utf-8', errors='ignore') as g:
                        newfilestoparse = None
                        data = g.read()
                        if f.name.lower().endswith('.toc'):
                            data = re.sub(r'\s*#.*$', '', data, flags=re.I | re.M)
                            newfilestoparse = re.findall(r'^\s*((?:(?<!\.\.).)+\.(?:xml|lua))\s*$', data,
                                                         flags=re.I | re.M)
                        elif f.name.lower().endswith('.xml'):
                            data = re.sub(r'<!--.*?-->', '', data, flags=re.I | re.S)
                            newfilestoparse = re.findall(r"<(?:Include|Script)\s+file=[\"']((?:(?<!\.\.).)+)[\"']\s*/>",
                                                         data, flags=re.I)
                        if newfilestoparse and len(newfilestoparse) > 0:
                            newfilestoparse = [Path(f.parent, element) for element in newfilestoparse]
                            self.parse_file(newfilestoparse)

    def normalize_content(self, content: bytes) -> bytes:
        """Normalize content by removing whitespace characters for fingerprinting."""
        # Whitespace characters to skip: tab (9), newline (10), carriage return (13), space (32)
        return bytes(b for b in content if b not in [9, 10, 13, 32])

    def compute_hash(self, data: bytes) -> int:
        """
        Compute MurmurHash2 exactly as CurseForge does.
        Based on: https://github.com/WowUp/WowUp/blob/master/wowup-electron/native/curse.cc
        """
        multiplex = 1540483477

        # Compute normalized length (length without whitespace)
        normalized_length = sum(1 for b in data if b not in [9, 10, 13, 32])

        # Initialize hash with seed XOR normalized length
        hash_val = 1 ^ normalized_length

        num3 = 0  # Accumulator for 32-bit chunks
        num4 = 0  # Bit shift counter (0, 8, 16, 24)

        for b in data:
            # Skip whitespace characters
            if b in [9, 10, 13, 32]:
                continue

            # Accumulate byte into 32-bit chunk
            num3 |= b << num4
            num4 += 8

            # Process complete 32-bit chunk
            if num4 == 32:
                num6 = (num3 * multiplex) & 0xFFFFFFFF
                num7 = ((num6 ^ (num6 >> 24)) * multiplex) & 0xFFFFFFFF
                hash_val = ((hash_val * multiplex) ^ num7) & 0xFFFFFFFF
                num3 = 0
                num4 = 0

        # Process remaining bytes
        if num4 > 0:
            hash_val = ((hash_val ^ num3) * multiplex) & 0xFFFFFFFF

        # Final avalanche mixing
        num6 = ((hash_val ^ (hash_val >> 13)) * multiplex) & 0xFFFFFFFF
        return (num6 ^ (num6 >> 15)) & 0xFFFFFFFF

    def compute_fingerprint(self, file_path: Path) -> int | None:
        """Compute MurmurHash2 fingerprint for a file."""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                return self.compute_hash(content)
        except Exception:
            return None

    def parse(self) -> None:
        for f in list(self.directory.glob('*')):
            if f.name.lower().endswith('.toc'):
                self.filesToParse.append(f)
            elif f.name.lower() == 'bindings.xml':
                self.filesToHash.append(f)
        self.parse_file(self.filesToParse)
        self.filesToHash = list(dict.fromkeys(self.filesToHash))

        # Compute individual file fingerprints
        for f in self.filesToHash:
            fingerprint = self.compute_fingerprint(f)
            if fingerprint is not None:
                self.individualFingerprints.append(fingerprint)

        # Sort fingerprints and compute folder fingerprint for reference
        self.individualFingerprints.sort()
        if self.individualFingerprints:
            fingerprints_string = ''.join(str(fp) for fp in self.individualFingerprints)
            # Use custom hash implementation for folder fingerprint too
            self.folderFingerprint = self.compute_hash(fingerprints_string.encode('ascii'))

    def get_individual_fingerprints(self) -> list[int]:
        """Return list of individual file fingerprints (sorted)"""
        return self.individualFingerprints

    def get_folder_fingerprint(self) -> int | None:
        """Return computed folder fingerprint (for caching/comparison)"""
        return self.folderFingerprint


def scan_directory_fingerprints(path: Path, directory: str) -> tuple[str, int | None]:
    """
    Scan a directory and compute its CurseForge fingerprint.

    Args:
        path: Base path to the addons directory
        directory: Directory name to scan

    Returns:
        Tuple of (directory, folder_fingerprint)
    """
    scanner = CurseForgeFingerprintScanner(path / directory)
    folder_fingerprint = scanner.get_folder_fingerprint()
    return directory, folder_fingerprint


def detect_curseforge_addons(
    http: httpx.Client, folder_fingerprints: dict[str, int | None], check_if_installed_dirs: Any
) -> tuple[list[str], list[str], list[str], set[str]]:
    """
    Detect CurseForge addons using fingerprint matching.

    Args:
        http: HTTP client instance
        folder_fingerprints: Dict of {directory: fingerprint}
        check_if_installed_dirs: Function to check if directories are already installed

    Returns:
        Tuple of (names, slugs, namesinstalled, matched_dirs)
    """
    names: list[str] = []
    namesinstalled: list[str] = []
    slugs: list[str] = []
    cf_matched_dirs: set[str] = set()

    # Flatten fingerprints for API call
    all_fingerprints = list(folder_fingerprints.values())

    if not all_fingerprints:
        return names, slugs, namesinstalled, cf_matched_dirs

    try:
        cf_payload = http.post('https://api.curseforge.com/v1/fingerprints/1',
                              json={'fingerprints': all_fingerprints},
                              headers={'x-api-key': CF_API_KEY},
                              timeout=30)
        if cf_payload.status_code != 200:
            return names, slugs, namesinstalled, cf_matched_dirs

        cf_data = cf_payload.json()['data']

        # Process exact matches
        mod_to_dirs_exact: dict[int, set[str]] = {}  # {modId: {dir1, dir2, ...}}
        exact_mod_ids: set[int] = set()

        if 'exactMatches' in cf_data:
            for match in cf_data['exactMatches']:
                # Filter for WoW addons only (gameId=1)
                if 'file' not in match or match['file'].get('gameId') != 1:
                    continue
                mod_id = match['id']
                exact_mod_ids.add(mod_id)
                # Check modules in the matched file to find which directory matched
                if 'modules' in match['file']:
                    for module in match['file']['modules']:
                        module_name = module['name']
                        # Find directory with matching name (case-insensitive)
                        for directory in folder_fingerprints.keys():
                            if directory.lower() == module_name.lower():
                                if mod_id not in mod_to_dirs_exact:
                                    mod_to_dirs_exact[mod_id] = set()
                                mod_to_dirs_exact[mod_id].add(directory)
                                cf_matched_dirs.add(directory)
                                break

        # Process partial matches (deduplicate against exact matches)
        mod_to_dirs_partial: dict[int, set[str]] = {}  # {modId: {dir1, dir2, ...}}

        if 'partialMatches' in cf_data:
            for match in cf_data['partialMatches']:
                # Filter for WoW addons only (gameId=1)
                if 'file' not in match or match['file'].get('gameId') != 1:
                    continue
                mod_id = match['id']
                # Skip if already in exact matches
                if mod_id in exact_mod_ids:
                    continue
                # Check modules in the matched file
                if 'modules' in match['file']:
                    for module in match['file']['modules']:
                        module_name = module['name']
                        # Find directory with matching name (case-insensitive)
                        for directory in folder_fingerprints.keys():
                            if directory.lower() == module_name.lower():
                                if mod_id not in mod_to_dirs_partial:
                                    mod_to_dirs_partial[mod_id] = set()
                                mod_to_dirs_partial[mod_id].add(directory)
                                cf_matched_dirs.add(directory)
                                break

        # Fetch mod details for all matched mod IDs
        all_mod_ids = list(set(mod_to_dirs_exact.keys()) | set(mod_to_dirs_partial.keys()))

        if all_mod_ids:
            try:
                mods_payload = http.post('https://api.curseforge.com/v1/mods',
                                        json={'modIds': all_mod_ids},
                                        headers={'x-api-key': CF_API_KEY},
                                        timeout=30)
                if mods_payload.status_code == 200:
                    mods_data = mods_payload.json()['data']
                    mod_details = {mod['id']: mod for mod in mods_data}

                    # Process exact matches first
                    for mod_id, directories in mod_to_dirs_exact.items():
                        if mod_id in mod_details:
                            mod = mod_details[mod_id]
                            addon_name = mod['name']
                            addon_slug = mod['slug']
                            sorted_dirs = sorted(directories)

                            # Check if already installed
                            if check_if_installed_dirs(sorted_dirs):
                                namesinstalled.append(addon_name)
                            else:
                                names.append(addon_name)
                                slugs.append(f'cf:{addon_slug}')

                    # Process partial matches after exact matches
                    for mod_id, directories in mod_to_dirs_partial.items():
                        if mod_id in mod_details:
                            mod = mod_details[mod_id]
                            addon_name = mod['name']
                            addon_slug = mod['slug']
                            sorted_dirs = sorted(directories)

                            if check_if_installed_dirs(sorted_dirs):
                                namesinstalled.append(addon_name)
                            else:
                                names.append(addon_name)
                                slugs.append(f'cf:{addon_slug}')
            except Exception:
                pass  # Continue with partial results if mod details fetch fails
    except Exception:
        pass  # Continue with partial results if CurseForge API fails

    return names, slugs, namesinstalled, cf_matched_dirs
