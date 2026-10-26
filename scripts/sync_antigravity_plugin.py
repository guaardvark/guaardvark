#!/usr/bin/env python3
"""
Build a standalone Antigravity plugin folder from this repo.

The plugin in .agents/plugins/guaardvark/ links its skills and launcher back
into the checkout, which works when Antigravity installs it from here. A copy
for anywhere else needs real files, so this script writes them: the skills in
.agents/skills/, the MCP launcher, the manifest, rules and assets, all taken
from a git ref, never from the working tree, so the copy cannot get ahead of
what is public.

Usage:
    python3 scripts/sync_antigravity_plugin.py <dest-dir> [--ref origin/main]

Then check it with `agy plugin validate <dest-dir> --strict`.
"""
import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLUGIN_SRC = ".agents/plugins/guaardvark"
# Repo path at the ref -> path inside the plugin folder.
COPIED_FILES = {
    "scripts/mcp_launcher.sh": "scripts/mcp_launcher.sh",
    f"{PLUGIN_SRC}/plugin.json": "plugin.json",
    f"{PLUGIN_SRC}/mcp_config.json": "mcp_config.json",
    f"{PLUGIN_SRC}/rules/AGENTS.md": "rules/AGENTS.md",
    f"{PLUGIN_SRC}/assets/logo.svg": "assets/logo.svg",
    f"{PLUGIN_SRC}/README.md": "README.md",
    "LICENSE": "LICENSE",
}
SKILLS_SRC = ".agents/skills"


def git_tar(ref, *paths):
    out = subprocess.run(
        ["git", "-C", project_root, "archive", "--format=tar", ref, *paths],
        check=True, capture_output=True,
    ).stdout
    return tarfile.open(fileobj=io.BytesIO(out))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dest", help="folder to write the plugin into")
    parser.add_argument("--ref", default="origin/main",
                        help="git ref to copy from (default: origin/main, i.e. what is public)")
    args = parser.parse_args()

    dest = os.path.abspath(args.dest)
    if os.path.abspath(project_root) == dest:
        sys.exit(f"destination cannot be the source repo root: {dest}")
    os.makedirs(dest, exist_ok=True)

    # Skills: replace the folder so a skill removed here disappears there too.
    skills_dest = os.path.join(dest, "skills")
    shutil.rmtree(skills_dest, ignore_errors=True)
    with git_tar(args.ref, SKILLS_SRC) as tar:
        for member in tar.getmembers():
            rel = os.path.relpath(member.name, SKILLS_SRC)
            # The skills README describes this repo's layout, not the plugin's.
            if not member.isfile() or rel == "README.md":
                continue
            target = os.path.join(skills_dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(tar.extractfile(member).read())

    with git_tar(args.ref, *COPIED_FILES) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            target = os.path.join(dest, COPIED_FILES[member.name])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(tar.extractfile(member).read())
            os.chmod(target, member.mode & 0o777)

    count = len(os.listdir(skills_dest))
    print(f"synced {count} skills, launcher, manifest, rules and assets from {args.ref} to {dest}")


if __name__ == "__main__":
    main()
