#!/usr/bin/env python3
"""
Sync the Claude plugin repository (github.com/guaardvark/guaardvark-plugin) from this repo.

Anthropic's plugin directory installs only the plugin folder and refuses large
files, so the directory listing is built from a separate repository that holds
the plugin alone. This repo stays the source of truth: the skills in
.agents/skills/, the MCP launcher, the logo and the manifest (its MCP server
split out into .mcp.json) are copied from a git ref, never from the working
tree, so the plugin cannot get ahead of what is public.

The plugin repo's README.md is its directory listing and is maintained there by
hand; this script never touches it.

Usage:
    python3 scripts/sync_claude_plugin.py <plugin-repo-checkout> [--ref origin/main]

Then, in the plugin checkout: review `git diff`, run
`claude plugin validate .`, commit and push. The directory picks up the push.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLUGIN_REPO_URL = "https://github.com/guaardvark/guaardvark-plugin"
# Repo path at the ref -> path inside the plugin repo.
COPIED_FILES = {
    "scripts/mcp_launcher.sh": "scripts/mcp_launcher.sh",
    "LICENSE": "LICENSE",
    "frontend/public/icon-512.png": "assets/logo.png",
}
LOGO = "assets/logo.png"
SKILLS_SRC = ".agents/skills"


def git_tar(ref, *paths):
    out = subprocess.run(
        ["git", "-C", project_root, "archive", "--format=tar", ref, *paths],
        check=True, capture_output=True,
    ).stdout
    return tarfile.open(fileobj=io.BytesIO(out))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dest", help="checkout of the guaardvark-plugin repository")
    parser.add_argument("--ref", default="origin/main",
                        help="git ref to copy from (default: origin/main, i.e. what is public)")
    args = parser.parse_args()

    dest = os.path.abspath(args.dest)
    if os.path.abspath(project_root) == dest or not os.path.isdir(dest):
        sys.exit(f"not a separate plugin checkout: {dest}")

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

    # Manifest: same name, version and MCP server as the marketplace manifest
    # here, with the skills path and repository of the plugin repo.
    manifest = json.loads(subprocess.run(
        ["git", "-C", project_root, "show", f"{args.ref}:.claude-plugin/plugin.json"],
        check=True, capture_output=True, text=True,
    ).stdout)
    manifest["skills"] = "./skills/"
    manifest["repository"] = PLUGIN_REPO_URL
    manifest["logo"] = LOGO
    # The server goes in a root .mcp.json, the one place every client reads it from:
    # plugin indexes such as cursor.directory find servers nowhere else, and Claude
    # Code and Grok load it the same as the inline entry.
    servers = manifest.pop("mcpServers")
    os.makedirs(os.path.join(dest, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(dest, ".claude-plugin", "plugin.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    with open(os.path.join(dest, ".mcp.json"), "w") as f:
        json.dump({"mcpServers": servers}, f, indent=2)
        f.write("\n")

    count = len(os.listdir(skills_dest))
    print(f"synced {count} skills, launcher, logo, LICENSE, .mcp.json and manifest "
          f"v{manifest['version']} from {args.ref}")
    subprocess.run(["git", "-C", dest, "status", "--short"], check=False)


if __name__ == "__main__":
    main()
