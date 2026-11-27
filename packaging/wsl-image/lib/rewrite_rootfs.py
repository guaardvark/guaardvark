"""Turn a `docker export` stream into a WSL root filesystem tar.

Docker adds /.dockerenv and owns /etc/hosts, /etc/hostname and /etc/resolv.conf
in a container; those come back from the base WSL image instead. /etc/machine-id
is written empty so systemd gives every installed copy its own id on first boot.
Whatever is left in /tmp and /var/tmp when the container stops (a daemon's
shutdown dump, for one) is dropped; the directories stay.
Ownership, modes, links and extended attributes pass through unchanged.

    docker export C | python3 -I rewrite_rootfs.py --base base.wsl --out out.tar --report sizes.txt
"""

import argparse
import io
import sys
import tarfile
from collections import defaultdict

FROM_BASE = {"etc/hosts", "etc/hostname", "etc/resolv.conf"}
DROP = {".dockerenv"}
MACHINE_ID = "etc/machine-id"
TEMP_DIRS = ("tmp/", "var/tmp/")


def norm(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.lstrip("/")


def base_members(path: str) -> dict:
    found = {}
    with tarfile.open(path, "r:*") as base:
        for m in base:
            n = norm(m.name)
            if n in FROM_BASE or n == MACHINE_ID:
                data = base.extractfile(m).read() if m.isfile() else None
                found[n] = (m, data)
            if len(found) == len(FROM_BASE) + 1:
                break
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    base = base_members(args.base)
    missing = (FROM_BASE | {MACHINE_ID}) - set(base)
    if missing:
        print(f"base image lacks {sorted(missing)}", file=sys.stderr)
        return 1

    by_dir = defaultdict(int)
    total = files = 0
    src = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
    with tarfile.open(args.out, "w", format=tarfile.PAX_FORMAT) as out:
        for m in src:
            n = norm(m.name)
            if n in DROP or n in FROM_BASE or n == MACHINE_ID:
                continue
            if n.startswith(TEMP_DIRS):
                print(f"dropped from the image: /{n}", file=sys.stderr)
                continue
            m.name = n if n else "."
            if m.isfile():
                out.addfile(m, src.extractfile(m))
                files += 1
                total += m.size
                parts = n.split("/")
                for depth in (1, 2, 3, 4):
                    if len(parts) > depth:
                        by_dir["/" + "/".join(parts[:depth])] += m.size
            else:
                out.addfile(m)
        for n in sorted(FROM_BASE | {MACHINE_ID}):
            m, data = base[n]
            m.name = n
            if n == MACHINE_ID:
                data = b""
                m.size = 0
                m.mode = 0o444
            out.addfile(m, io.BytesIO(data) if data is not None else None)

    with open(args.report, "w") as rep:
        rep.write(f"regular files: {files}\nfile bytes:    {total} ({total / 2**30:.2f} GiB)\n\n")
        rep.write("biggest directories (file bytes, GiB):\n")
        for d, size in sorted(by_dir.items(), key=lambda kv: -kv[1])[:40]:
            rep.write(f"  {size / 2**30:7.2f}  {d}\n")
    print(open(args.report).read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
