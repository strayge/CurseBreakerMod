import shutil
import tempfile
import zipfile
from typing import Any
from pathlib import Path
from difflib import unified_diff


CLEAN_BACKUP_DIR = 'WTF/CurseBreakerClean'


class ModManager:
    def __init__(self, core: Any) -> None:
        self.core: Any = core

    def create_mod(self, addon_name: str, mod_name: str) -> int:
        """Create mod from current addon modifications"""
        # 1. Validate addon exists and is installed
        addon = self.core.check_if_installed(addon_name)
        if not addon:
            raise RuntimeError(f"Addon '{addon_name}' is not installed.")

        # 2. Check if clean ZIP exists
        clean_zip = Path(f'{CLEAN_BACKUP_DIR}/{addon_name}/{addon["Version"]}.zip')
        if not clean_zip.exists():
            raise RuntimeError(
                f"No clean version found for {addon_name} v{addon['Version']}.\n"
                f"Run 'force_update {addon_name}' to reinstall cleanly first."
            )

        # 3. Create single temp directory with subdirectories
        temp_base = Path(tempfile.mkdtemp(prefix='cb_mod_'))

        try:
            # 4. Extract clean version to temp_base/clean
            clean_path = temp_base / 'clean'
            clean_path.mkdir()
            with zipfile.ZipFile(clean_zip, 'r') as zf:
                zf.extractall(clean_path)

            # 5. Copy clean to expected (copytree creates expected_path for us)
            expected_path = temp_base / 'expected'
            shutil.copytree(clean_path, expected_path)

            # Apply existing enabled mods to expected
            enabled_mods = self._get_enabled_mods(addon_name)
            for _mod_name, mod_data in enabled_mods:
                self._apply_mod(expected_path, mod_data, raise_on_error=False)

            # 6. Generate diffs between expected and current
            current_path = Path('Interface/AddOns')
            patches, added, removed = self._compare_directories(
                expected_path, current_path, addon['Directories'], addon['Version']
            )

            # 7. Validate we found changes
            if not patches and not added and not removed:
                raise RuntimeError("No modifications detected. Edit files before creating mod.")

        finally:
            # 8. Always cleanup temp directory
            shutil.rmtree(temp_base, ignore_errors=True)

        # 9. Save mod to config
        if addon_name not in self.core.config['Mods']:
            self.core.config['Mods'][addon_name] = {}

        if mod_name in self.core.config['Mods'][addon_name]:
            raise RuntimeError(f"Mod '{mod_name}' already exists. Delete it first or choose different name.")

        self.core.config['Mods'][addon_name][mod_name] = {
            'enabled': True,
            'priority': self._get_next_priority(addon_name),
            'baseVersion': addon['Version'],
            'patches': patches,
            'added': added,
            'removed': removed
        }

        self.core.save_config()
        return len(patches) + len(added) + len(removed)

    def _get_enabled_mods(self, addon_name: str) -> list[tuple[str, dict[str, Any]]]:
        """Get enabled mods for an addon, sorted by priority"""
        if addon_name not in self.core.config['Mods']:
            return []

        enabled_mods = [
            (name, data) for name, data in self.core.config['Mods'][addon_name].items()
            if data.get('enabled', False)
        ]
        enabled_mods.sort(key=lambda x: x[1].get('priority', 999))
        return enabled_mods

    def _apply_mod(self, base_path: Path, mod_data: dict[str, Any], raise_on_error: bool = True) -> None:
        """Apply a single mod's changes to the specified base path.

        Args:
            base_path: The directory to apply changes to (e.g., Interface/AddOns or temp dir)
            mod_data: The mod configuration containing patches, added, and removed
            raise_on_error: If True, raise exceptions on failure. If False, silently skip failures.
        """
        # Apply patches to modified files
        for file_path, patch_content in mod_data.get('patches', {}).items():
            target_file = base_path / file_path
            if not target_file.exists():
                if raise_on_error:
                    raise RuntimeError(f'File {file_path} not found. The file may have been removed.')
                continue
            try:
                self._apply_patch(target_file, patch_content)
            except Exception:
                if raise_on_error:
                    raise

        # Create added files
        for file_path, file_content in mod_data.get('added', {}).items():
            target_file = base_path / file_path
            try:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with open(target_file, 'w', encoding='utf-8') as f:
                    f.write(file_content)
            except Exception:
                if raise_on_error:
                    raise

        # Remove deleted files
        for file_path in mod_data.get('removed', []):
            target_file = base_path / file_path
            try:
                if target_file.exists():
                    target_file.unlink()
            except Exception:
                if raise_on_error:
                    raise

    def _compare_directories(
        self, expected_path: Path, current_path: Path, directories: list[str], version: str
    ) -> tuple[dict[str, str], dict[str, str], list[str]]:
        """Compare files between expected and current directories.

        Returns:
            patches: dict of file path -> unified diff for modified files
            added: dict of file path -> full content for new files
            removed: list of file paths that were deleted
        """
        patches: dict[str, str] = {}
        added: dict[str, str] = {}
        removed: list[str] = []

        for directory in directories:
            current_dir = current_path / directory
            expected_dir = expected_path / directory

            # Collect all lua/xml files from both directories
            current_files: set[Path] = set()
            expected_files: set[Path] = set()

            if current_dir.exists():
                for file_path in self._get_lua_xml_files(current_dir):
                    current_files.add(file_path.relative_to(current_path))

            if expected_dir.exists():
                for file_path in self._get_lua_xml_files(expected_dir):
                    expected_files.add(file_path.relative_to(expected_path))

            # Files in both: check for modifications
            for rel_path in current_files & expected_files:
                diff = self._generate_diff(expected_path / rel_path, current_path / rel_path, version)
                if diff:
                    patches[rel_path.as_posix()] = diff

            # Files only in current: added files
            for rel_path in current_files - expected_files:
                try:
                    with open(current_path / rel_path, encoding='utf-8', errors='ignore') as f:
                        added[rel_path.as_posix()] = f.read()
                except Exception:
                    pass

            # Files only in expected: removed files
            for rel_path in expected_files - current_files:
                removed.append(rel_path.as_posix())

        return patches, added, removed

    def _generate_diff(self, expected_file: Path, current_file: Path, version: str) -> str | None:
        """Generate unified diff between two files"""
        try:
            with open(expected_file, encoding='utf-8', errors='ignore') as f:
                expected_lines = f.readlines()
            with open(current_file, encoding='utf-8', errors='ignore') as f:
                current_lines = f.readlines()

            diff_lines = list(unified_diff(
                expected_lines,
                current_lines,
                fromfile=f'{expected_file.name} (base v{version})',
                tofile=f'{current_file.name} (modded)',
                lineterm=''
            ))

            return '\n'.join(diff_lines) if diff_lines else None
        except Exception:
            return None

    def _apply_patch(self, target_file: Path, patch_content: str) -> None:
        """Apply unified diff patch to file with context-aware matching"""
        with open(target_file, encoding='utf-8', errors='ignore') as f:
            original_lines = f.readlines()

        # Parse patch
        patch_lines = patch_content.split('\n')
        hunks: list[dict[str, Any]] = []
        current_hunk: dict[str, Any] | None = None

        for line in patch_lines:
            if line.startswith('@@'):
                if current_hunk:
                    hunks.append(current_hunk)
                # Parse hunk header: @@ -start,count +start,count @@
                parts = line.split('@@')[1].strip().split()
                old_start = int(parts[0].split(',')[0][1:])
                new_start = int(parts[1].split(',')[0][1:])
                current_hunk = {
                    'old_start': old_start,
                    'new_start': new_start,
                    'lines': []
                }
            elif current_hunk is not None and line.startswith(('+', '-', ' ')):
                current_hunk['lines'].append(line)

        if current_hunk:
            hunks.append(current_hunk)

        # Apply hunks in reverse order to maintain line numbers
        result_lines = original_lines.copy()

        for hunk in reversed(hunks):
            # Find the actual position using context-aware matching
            actual_start = self._find_hunk_position(result_lines, hunk)
            current_idx = actual_start

            for line in hunk['lines']:
                if line.startswith('-'):
                    # Remove line from result
                    if current_idx < len(result_lines):
                        del result_lines[current_idx]
                elif line.startswith('+'):
                    # Insert new line
                    new_content = line[1:]
                    if not new_content.endswith('\n'):
                        new_content += '\n'
                    result_lines.insert(current_idx, new_content)
                    current_idx += 1
                elif line.startswith(' '):
                    # Context line - just advance index
                    current_idx += 1

        # Write patched content
        with open(target_file, 'w', encoding='utf-8', errors='ignore') as f:
            f.writelines(result_lines)

    def _find_hunk_position(self, lines: list[str], hunk: dict[str, Any]) -> int:
        """Find the actual position in file where hunk should be applied.

        Uses context lines from the hunk to find the correct position,
        allowing patches to work even when line numbers have shifted.
        """
        # Normalize lines for comparison
        def normalize(s: str) -> str:
            return s.rstrip('\n')

        # Build a list of (type, content) for context/removed lines with their positions
        # We need to find where in the target file the changes should be applied
        hunk_entries: list[tuple[str, str]] = []  # (type: ' ' or '-', normalized content)
        for line in hunk['lines']:
            if line.startswith(' '):
                hunk_entries.append((' ', normalize(line[1:])))
            elif line.startswith('-'):
                hunk_entries.append(('-', normalize(line[1:])))

        if not hunk_entries:
            return max(0, hunk['old_start'] - 1)

        # Find the index of the first '-' line in hunk_entries
        first_change_idx = None
        for i, (typ, _) in enumerate(hunk_entries):
            if typ == '-':
                first_change_idx = i
                break

        if first_change_idx is None:
            # No removed lines - this is a pure addition, use context to find position
            first_change_idx = 0

        # Extract lines to match: context before first change + the changed lines
        # We'll use up to 2 context lines before the change plus the changed lines
        context_before_count = min(first_change_idx, 2)
        start_idx = first_change_idx - context_before_count

        # Collect the core lines to match (context before + removed lines)
        core_lines: list[str] = []
        for i in range(start_idx, len(hunk_entries)):
            typ, content = hunk_entries[i]
            core_lines.append(content)
            # Stop after we've collected all the '-' lines plus one context after
            if i > first_change_idx and typ == ' ':
                break

        if not core_lines:
            return max(0, hunk['old_start'] - 1)

        # Normalize target file lines
        normalized_lines = [normalize(line) for line in lines]

        # Search for matching sequence in the file
        # Start searching near the original position, expanding outward
        original_pos = max(0, hunk['old_start'] - 1)
        max_search_distance = len(lines) + 1

        for offset in range(max_search_distance):
            # Try position before and after original
            for pos in [original_pos - offset, original_pos + offset]:
                search_start = pos - context_before_count
                if search_start < 0 or search_start + len(core_lines) > len(lines):
                    continue

                # Check if core lines match at this position
                match = True
                for i, exp_line in enumerate(core_lines):
                    if normalized_lines[search_start + i] != exp_line:
                        match = False
                        break

                if match:
                    # Return the position where the hunk should start applying
                    # (accounting for any leading context in the original hunk)
                    return search_start + context_before_count - first_change_idx

        # No match found - raise error
        raise RuntimeError(
            f'Could not find matching context for patch hunk at line {hunk["old_start"]}. '
            'The target code may have been modified or removed.'
        )

    def _get_lua_xml_files(self, directory: Path) -> Any:
        """Get all .lua and .xml files recursively"""
        for ext in ['**/*.lua', '**/*.xml']:
            yield from directory.glob(ext)

    def _get_next_priority(self, addon_name: str) -> int:
        """Get next available priority number"""
        if addon_name not in self.core.config['Mods']:
            return 1
        priorities = [m.get('priority', 1) for m in self.core.config['Mods'][addon_name].values()]
        return max(priorities, default=0) + 1

    def reapply_all_mods(self, addon_name: str) -> list[tuple[str, str]]:
        """Reapply all enabled mods for an addon"""
        addon = self.core.check_if_installed(addon_name)
        if not addon:
            return []

        # Get enabled mods sorted by priority
        enabled_mods = self._get_enabled_mods(addon_name)
        if not enabled_mods:
            return []

        failed_mods: list[tuple[str, str]] = []
        current_path = Path('Interface/AddOns')

        for mod_name, mod_data in enabled_mods:
            try:
                self._apply_mod(current_path, mod_data, raise_on_error=True)
            except Exception as e:
                # Disable failed mod
                mod_data['enabled'] = False
                failed_mods.append((mod_name, str(e)))

        if failed_mods:
            self.core.save_config()

        return failed_mods
