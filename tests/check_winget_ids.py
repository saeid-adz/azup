#!/usr/bin/env python3
"""Fails if a winget package id used by azup does not exist in microsoft/winget-pkgs.

Catches typos before users do ("No package found matching input criteria").
Uses GITHUB_TOKEN if set (CI provides one) to avoid GitHub's anonymous rate limit.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import azup  # noqa: E402

ids = {t.winget: t.name for t in azup.CATALOG if t.winget}
ids["Python.Python.3.12"] = "Python (install.ps1)"
bad = []
for pid, name in sorted(ids.items()):
    parts = pid.split(".")
    url = ("https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/"
           + parts[0][0].lower() + "/" + "/".join(parts))
    try:
        azup.gh_json(url)
        print(f"ok       {pid}")
    except Exception as e:
        print(f"MISSING  {pid}  ({name}): {e}")
        bad.append(pid)
sys.exit(1 if bad else 0)
