#!/usr/bin/python3
"""
PlatformIO pre-build script to automatically set firmware version and build date.
Generates version string like "v1.0.12-70f39d3" from git describe + commit hash.
"""

import subprocess
import datetime
import os

Import("env")

def get_git_version():
    """Get version from git tags or fallback to commit hash"""
    try:
        # Try to get version from git tag (e.g., v1.0.12)
        version = subprocess.check_output(
            ['git', 'describe', '--tags', '--abbrev=0'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback if no tags exist
        version = "v0.0.0"
    
    return version

def get_git_commit_hash():
    """Get short commit hash"""
    try:
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit_hash = "unknown"
    
    return commit_hash

def get_build_date():
    """Get current date in format: 16-Jun-2026"""
    return datetime.datetime.now().strftime('%d-%b-%Y')

# Check if environment variable overrides exist
firmware_version = os.environ.get("FIRMWARE_VERSION")
firmware_build_date = os.environ.get("FIRMWARE_BUILD_DATE")

# Generate version info if not provided by environment
if not firmware_version:
    version = get_git_version()
    commit_hash = get_git_commit_hash()
    firmware_version = f"{version}-{commit_hash}"

if not firmware_build_date:
    firmware_build_date = get_build_date()

def cpp_string_define(value: str) -> str:
    """Format a value for CPPDEFINES so the macro expands to a C string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'\\"{escaped}\\"'


# Add defines to build
env.Append(CPPDEFINES=[
    ("FIRMWARE_VERSION", cpp_string_define(firmware_version)),
    ("FIRMWARE_BUILD_DATE", cpp_string_define(firmware_build_date)),
])

print(f"Building with version: {firmware_version} ({firmware_build_date})")
