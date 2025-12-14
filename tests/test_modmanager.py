# pyright: reportPrivateUsage=false
import shutil
import tempfile
from pathlib import Path
from textwrap import dedent
from unittest.mock import Mock

import pytest

from CB.ModManager import ModManager


@pytest.fixture
def mod_manager() -> ModManager:
    return ModManager(core=Mock())


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    tmp = Path(tempfile.mkdtemp(prefix='test_modmanager_'))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def apply_and_verify(
    mod_manager: ModManager,
    temp_dir: Path,
    initial_files: dict[str, str],
    expected_files: dict[str, str],
) -> dict[str, dict[str, str] | list[str]]:
    """
    Test helper: creates files, uses _compare_directories to generate mod_data, applies mod, verifies.

    Args:
        mod_manager: ModManager instance
        temp_dir: Temporary directory for test files
        initial_files: {relative_path: content} - files before mod application
        expected_files: {relative_path: content} - expected files after mod application
    """
    # 1. Write initial_files to 'initial' directory
    initial_path = temp_dir / 'initial'
    for path, content in initial_files.items():
        file_path = initial_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # 2. Write expected_files to 'modified' directory
    modified_path = temp_dir / 'modified'
    for path, content in expected_files.items():
        file_path = modified_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # 3. Extract directory names from file paths (e.g., 'MyAddon' from 'MyAddon/core.lua')
    directories = sorted({
        path.split('/')[0]
        for path in initial_files.keys()
    })

    # 4. Use actual _compare_directories to generate mod_data
    patches, added, removed = mod_manager._compare_directories(
        initial_path, modified_path, directories, '1.0.0'
    )
    mod_data = {'patches': patches, 'added': added, 'removed': removed}

    mod_manager._apply_mod(initial_path, mod_data)

    # 6. Assert target matches expected_files
    for path, content in expected_files.items():
        file_path = initial_path / path
        assert file_path.exists(), f'Expected file {path} to exist'
        assert file_path.read_text() == content, f'Content mismatch for {path}'

    # 7. Assert removed files don't exist in target
    for path in removed:
        # Normalize path separators for cross-platform
        normalized = path.replace('\\', '/')
        if normalized in initial_files:
            file_path = initial_path / normalized
            assert not file_path.exists(), f'Expected file {path} to be removed'

    return mod_data


def test_no_changes_produces_no_diff(mod_manager: ModManager, temp_dir: Path):
    """Identical files should produce no diff (empty mod_data)."""
    mod_data = apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'line1\nline2\nline3\n'},
        expected_files={'MyAddon/core.lua': 'line1\nline2\nline3\n'},
    )
    assert mod_data['patches'] == {}
    assert mod_data['added'] == {}
    assert mod_data['removed'] == []


def test_add_single_line(mod_manager: ModManager, temp_dir: Path):
    """Adding a line to existing file."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'line1\nline2\nline3\n'},
        expected_files={'MyAddon/core.lua': 'line1\nline2\nnew_line\nline3\n'},
    )


def test_remove_single_line(mod_manager: ModManager, temp_dir: Path):
    """Removing a line from existing file."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'line1\nline2\nline3\n'},
        expected_files={'MyAddon/core.lua': 'line1\nline3\n'},
    )


def test_modify_single_line(mod_manager: ModManager, temp_dir: Path):
    """Modifying a line in existing file."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'line1\nold_value\nline3\n'},
        expected_files={'MyAddon/core.lua': 'line1\nnew_value\nline3\n'},
    )


def test_add_content_to_empty_file(mod_manager: ModManager, temp_dir: Path):
    """Adding content to empty file."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': ''},
        expected_files={'MyAddon/core.lua': 'new_line1\nnew_line2\n'},
    )


def test_add_new_file(mod_manager: ModManager, temp_dir: Path):
    """Adding a new file to addon."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'original\n'},
        expected_files={
            'MyAddon/core.lua': 'original\n',
            'MyAddon/newfile.lua': 'new content\n',
        },
    )


def test_add_nested_file(mod_manager: ModManager, temp_dir: Path):
    """Adding a new file in nested directory structure."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': 'original\n'},
        expected_files={
            'MyAddon/core.lua': 'original\n',
            'MyAddon/Libs/SubLib/init.lua': 'nested content\n',
        },
    )


def test_remove_file(mod_manager: ModManager, temp_dir: Path):
    """Removing a file from addon."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={
            'MyAddon/core.lua': 'keep\n',
            'MyAddon/unwanted.lua': 'delete me\n',
        },
        expected_files={'MyAddon/core.lua': 'keep\n'},
    )


def test_combined_operations(mod_manager: ModManager, temp_dir: Path):
    """Patch + add + remove in single mod."""
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={
            'MyAddon/core.lua': 'line1\nline2\n',
            'MyAddon/old.lua': 'old content\n',
        },
        expected_files={
            'MyAddon/core.lua': 'line1\nchanged\n',
            'MyAddon/new.lua': 'brand new\n',
        },
    )


def test_realistic_lua_changes(mod_manager: ModManager, temp_dir: Path):
    """Realistic Lua code modifications."""
    original = dedent('''\
        local function MyFunction()
            local setting = true
            if setting then
                print("Hello")
            end
        end
        ''')
    modified = dedent('''\
        local function MyFunction()
            local setting = false
            local newVar = "test"
            if setting then
                print("Hello World")
            end
        end
        ''')
    apply_and_verify(
        mod_manager, temp_dir,
        initial_files={'MyAddon/core.lua': original},
        expected_files={'MyAddon/core.lua': modified},
    )


def test_apply_mod_to_updated_file_with_shifted_lines(mod_manager: ModManager, temp_dir: Path):
    """
    Test applying a mod to an updated file where target lines have shifted.

    Scenario:
    1. Original addon v1.0 has a function at lines 5-10
    2. User creates mod changing line 7
    3. Addon updates to v2.0, adding new code at top - function now at lines 15-20
    4. Mod should still apply correctly to the shifted location
    """
    # Original file (v1.0) - the version mod was created against
    original_v1 = dedent('''\
        -- MyAddon v1.0
        local MyAddon = {}

        function MyAddon:Init()
            self.enabled = true
            self.debug = false
        end

        return MyAddon
        ''')

    # User's modified version (mod changes debug to true)
    user_modified = dedent('''\
        -- MyAddon v1.0
        local MyAddon = {}

        function MyAddon:Init()
            self.enabled = true
            self.debug = true
        end

        return MyAddon
        ''')

    # Updated file (v2.0) - new code added at top, shifting the function down
    updated_v2 = dedent('''\
        -- MyAddon v2.0
        -- New features in this version!
        local MyAddon = {}

        -- New configuration system
        local defaults = {
            scale = 1.0,
            alpha = 0.8,
        }

        function MyAddon:Init()
            self.enabled = true
            self.debug = false
        end

        function MyAddon:ApplyDefaults()
            for k, v in pairs(defaults) do
                self[k] = v
            end
        end

        return MyAddon
        ''')

    # Expected result: v2.0 with the user's mod applied (debug = true)
    expected_result = dedent('''\
        -- MyAddon v2.0
        -- New features in this version!
        local MyAddon = {}

        -- New configuration system
        local defaults = {
            scale = 1.0,
            alpha = 0.8,
        }

        function MyAddon:Init()
            self.enabled = true
            self.debug = true
        end

        function MyAddon:ApplyDefaults()
            for k, v in pairs(defaults) do
                self[k] = v
            end
        end

        return MyAddon
        ''')

    # Step 1: Create mod by comparing original v1 with user modified version
    original_path = temp_dir / 'original_v1'
    modified_path = temp_dir / 'modified'

    for path, content in [('MyAddon/core.lua', original_v1)]:
        file_path = original_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    for path, content in [('MyAddon/core.lua', user_modified)]:
        file_path = modified_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    patches, added, removed = mod_manager._compare_directories(
        original_path, modified_path, ['MyAddon'], '1.0.0'
    )
    mod_data = {'patches': patches, 'added': added, 'removed': removed}

    # Verify mod was created
    assert 'MyAddon/core.lua' in patches, 'Patch should be created for modified file'

    # Step 2: Create updated v2 directory (simulating addon update)
    updated_path = temp_dir / 'updated_v2'
    for path, content in [('MyAddon/core.lua', updated_v2)]:
        file_path = updated_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # Step 3: Apply the mod created against v1 to the updated v2 file
    mod_manager._apply_mod(updated_path, mod_data)

    # Step 4: Verify the mod was applied correctly despite line number changes
    result_file = updated_path / 'MyAddon/core.lua'
    assert result_file.exists(), 'File should exist after mod application'
    assert result_file.read_text() == expected_result, (
        'Mod should apply correctly to updated file with shifted line numbers'
    )


def test_apply_mod_fails_when_target_code_removed(mod_manager: ModManager, temp_dir: Path):
    """
    Test that mod application fails when the code being modified has been removed.

    Scenario:
    1. Original addon v1.0 has a function MyAddon:Init()
    2. User creates mod changing a line in that function
    3. Addon updates to v2.0, completely removing the Init() function
    4. Mod should fail to apply since the target code no longer exists
    """
    # Original file (v1.0) - the version mod was created against
    original_v1 = dedent('''\
        -- MyAddon v1.0
        local MyAddon = {}

        function MyAddon:Init()
            self.enabled = true
            self.debug = false
        end

        return MyAddon
        ''')

    # User's modified version (mod changes debug to true)
    user_modified = dedent('''\
        -- MyAddon v1.0
        local MyAddon = {}

        function MyAddon:Init()
            self.enabled = true
            self.debug = true
        end

        return MyAddon
        ''')

    # Updated file (v2.0) - Init() function completely removed
    updated_v2 = dedent('''\
        -- MyAddon v2.0
        -- Complete rewrite!
        local MyAddon = {}

        function MyAddon:NewAPI()
            self.version = 2
        end

        return MyAddon
        ''')

    # Step 1: Create mod by comparing original v1 with user modified version
    original_path = temp_dir / 'original_v1'
    modified_path = temp_dir / 'modified'

    for path, content in [('MyAddon/core.lua', original_v1)]:
        file_path = original_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    for path, content in [('MyAddon/core.lua', user_modified)]:
        file_path = modified_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    patches, added, removed = mod_manager._compare_directories(
        original_path, modified_path, ['MyAddon'], '1.0.0'
    )
    mod_data = {'patches': patches, 'added': added, 'removed': removed}

    # Verify mod was created
    assert 'MyAddon/core.lua' in patches, 'Patch should be created for modified file'

    # Step 2: Create updated v2 directory (simulating addon update with removed code)
    updated_path = temp_dir / 'updated_v2'
    for path, content in [('MyAddon/core.lua', updated_v2)]:
        file_path = updated_path / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    # Step 3: Apply the mod - should fail since target code was removed
    with pytest.raises(RuntimeError, match='Could not find matching context'):
        mod_manager._apply_mod(updated_path, mod_data)
