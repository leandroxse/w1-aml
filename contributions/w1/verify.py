#!/usr/bin/env python3
"""Read-only patch checks and source-derived host models; no kernel/hardware test."""
import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

BASE = "fc5417f19706f85bccb125c1f951c787cf9362f5"
FILES = ("wifi_mac_sta.c", "wifi_mac_xmit.c", "wifi_mac_var.h", "osdep.h")
PREFIX = "project_w1/vmac/"
PATCHES = ("0001-fix-station-free-allocation.patch", "0002-fix-early-tx-drop-tid.patch")


def run(command, cwd=None):
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or str(command))
    return result.stdout


def one(pattern, source):
    matches = re.findall(pattern, source, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one source match: " + pattern)
    return matches[0]


def model(before, after, temporary):
    """Use actual allocation, init and counter statements in a safe host harness.

    Back the counter with 256 entries so the unfixed byte index cannot cause C
    undefined behavior in this harness. Entries 8..255 represent writes outside
    the REAL driver's eight-entry array. This does not emulate kernel memory.
    Invalid classified TIDs are synthetic defensive tests, not observed outputs
    of wifi_mac_classify().
    """
    one(r"^#define NET_MALLOC\(_size, _gfp, _str\) ZMALLOC\(ALIGN\(_size \+ 1, 128\), _str, _gfp\)$", before["osdep.h"])
    one(r"^    unsigned int (sts_tx_tid_drop_msdu\[8\]);$", before["wifi_mac_var.h"])
    structure = one(r"(struct nsta_entry\s*\{[^}]+\};)", before["wifi_mac_var.h"])
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or not shutil.which(compiler[0]):
        raise RuntimeError("C compiler missing; install cc or set CC")
    for label, sources in (("before", before), ("after", after)):
        allocation = one(r"del_nsta = \(struct nsta_entry \*\)NET_MALLOC\((sizeof\(struct nsta_entry(?: \*)?\)),", sources["wifi_mac_sta.c"])
        function = sources["wifi_mac_xmit.c"].split("netdev_tx_t wifi_mac_hardstart(", 1)[1].split("\nvoid wifi_mac_send_setup", 1)[0]
        early = function.split("goto bad;", 1)[0]
        initialization = "os_skb_set_tid(skb, 0);" if "os_skb_set_tid(skb, 0);" in early else ""
        drop = one(r"((?:            if \(os_skb_get_tid\(skb\) < WME_NUM_TID\)\n)?\s*wnet_vif->vif_sts.sts_tx_tid_drop_msdu\[os_skb_get_tid\(skb\)\]\+\+;)", function.split("\nbad:", 1)[1])
        if (label == "after") != bool(initialization):
            raise RuntimeError("Unexpected early TID initialization")
        source = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct list_head { struct list_head *next, *prev; };
struct wifi_station;
STRUCTURE
#define WME_NUM_TID 8
struct sk_buff { unsigned char tid; };
struct vif { struct { unsigned int sts_tx_tid_drop_msdu[256]; } vif_sts; };
static int os_skb_get_tid(struct sk_buff *skb) { return skb->tid; }
#define os_skb_set_tid(skb, value) ((skb)->tid = (value))
static void count(struct vif *wnet_vif, struct sk_buff *skb, int classified)
{
    INITIALIZATION
    if (classified >= 0) skb->tid = (unsigned char)classified;
    DROP
}
int main(void)
{
    size_t requested = ALLOCATION;
    size_t actual = (requested + 1 + 127) & ~(size_t)127;
    unsigned int early = 0, defensive = 0;
    assert(sizeof(void *) == 8 && sizeof(struct nsta_entry) == 32);
    assert(actual == 128);
    for (int value = 0; value < 256; value++) {
        for (int path = 0; path < 2; path++) {
            struct vif vif = {0};
            struct sk_buff skb = { .tid = (unsigned char)value };
            count(&vif, &skb, path ? value : -1);
            unsigned int outside = 0, inside = 0;
            for (int i = 0; i < 256; i++) {
                if (i < 8) inside += vif.vif_sts.sts_tx_tid_drop_msdu[i];
                else outside += vif.vif_sts.sts_tx_tid_drop_msdu[i];
            }
            if (path) defensive += outside; else early += outside;
            if (value < 8) assert(inside == 1 && outside == 0);
        }
    }
    printf("%zu %zu %u %u\n", requested, actual, early, defensive);
    return 0;
}
'''
        for marker, content in (("STRUCTURE", structure), ("INITIALIZATION", initialization), ("DROP", drop), ("ALLOCATION", allocation)):
            source = source.replace(marker, content)
        cfile, binary = temporary / (label + ".c"), temporary / label
        cfile.write_text(source)
        run(compiler + ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", str(cfile), "-o", str(binary)])
        values = run([str(binary)]).strip()
        expected = "8 128 248 248" if label == "before" else "32 128 0 0"
        if values != expected:
            raise RuntimeError(label + " model mismatch: " + values)
        print(label + ": requested bytes, allocated bytes, early invalid writes, synthetic classified invalid writes: " + values)


def verify(checkout):
    # Disable Git's optional index refresh; do not write checkout metadata.
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    root = checkout.resolve()
    if Path(run(["git", "-C", str(root), "rev-parse", "--show-toplevel"]).strip()).resolve() != root:
        raise RuntimeError("Pass the upstream repository root")
    git = ["git", "-C", str(root)]
    actual = run(git + ["rev-parse", "HEAD"]).strip()
    if actual != BASE:
        raise RuntimeError("Wrong base: expected " + BASE + ", got " + actual)
    if run(git + ["status", "--porcelain", "--untracked-files=no"]).strip():
        raise RuntimeError("Tracked files modified; use a clean upstream checkout")
    with tempfile.TemporaryDirectory(prefix="w1-verify-") as directory:
        temporary = Path(directory)
        # Copy only four pinned source files, not the whole vendor tree.
        for name in FILES:
            target = temporary / PREFIX / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(run(git + ["show", "HEAD:" + PREFIX + name]))
        before = {name: (temporary / PREFIX / name).read_text() for name in FILES}
        for name in PATCHES:
            patch = Path(__file__).resolve().parent / name
            # Apply trusted package files only inside the disposable copy.
            run(["git", "apply", "--check", str(patch)], cwd=temporary)
            run(["git", "apply", str(patch)], cwd=temporary)
        after = {name: (temporary / PREFIX / name).read_text() for name in FILES}
        if {name for name in FILES if before[name] != after[name]} != {"wifi_mac_sta.c", "wifi_mac_xmit.c"}:
            raise RuntimeError("Unexpected source changes")
        actual_files = {p.relative_to(temporary).as_posix() for p in temporary.rglob("*") if p.is_file()}
        if actual_files != {PREFIX + name for name in FILES}:
            raise RuntimeError("Patch created unexpected files")
        model(before, after, temporary)
    print("PASS: exact base, clean tracked tree, both patches, source-derived host models")
    print("Scope: no kernel/module build, no hardware test; classified invalid TIDs are synthetic.")
    print("Allocation correction is semantic: both requests allocate 128 bytes in this backend.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    try:
        verify(parser.parse_args().checkout)
    except (RuntimeError, OSError) as error:
        parser.exit(1, "FAIL: " + str(error) + "\n")
