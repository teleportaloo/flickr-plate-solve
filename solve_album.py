#!/usr/bin/env python3
"""
Plate-solve all photos in a Flickr album.

Walks through every photo in an album and runs plate_solve.py on each.
Skips photos that already have astrometry:status=solved or
astrometry:status=failed tags (use --force to re-solve those).

Usage:
    python3 solve_album.py https://www.flickr.com/photos/major_clanger/albums/72177720326735849/
    python3 solve_album.py --dry-run 72177720326735849
    python3 solve_album.py --force 72177720326735849

Each solve can take up to 30 minutes, so expect this to run for a while!
"""

import sys
import re
import argparse
import subprocess

from plate_solve import load_flickr_api


def get_album_id(arg):
    """Extract album/photoset ID from a Flickr URL or bare ID."""
    m = re.search(r'/albums/(\d+)', arg)
    if m:
        return m.group(1)
    if arg.strip().isdigit():
        return arg.strip()
    print(f"Could not extract album ID from: {arg}", file=sys.stderr)
    sys.exit(1)


def get_astrometry_status(flickr, photo_id):
    """Check if a photo has an astrometry:status tag.

    Returns 'solved', 'failed', or None.
    """
    try:
        resp = flickr.oauth.call_method('flickr.photos.getInfo', photo_id=photo_id)
        tags = resp.findall('.//tag')
        for tag in tags:
            raw = tag.get('raw', '')
            if raw == 'astrometry:status=solved':
                return 'solved'
            if raw == 'astrometry:status=failed':
                return 'failed'
    except Exception as e:
        print(f"    Error checking tags: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Plate-solve all photos in a Flickr album')
    parser.add_argument('album', help='Flickr album URL or photoset ID')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without changing anything')
    parser.add_argument('--force', action='store_true',
                        help='Re-solve photos even if already solved or failed')
    parser.add_argument('--no-comment', action='store_true',
                        help='Skip adding comments')
    parser.add_argument('--no-note', action='store_true',
                        help='Skip adding notes')
    args = parser.parse_args()

    album_id = get_album_id(args.album)
    print(f"Album: {album_id}")

    flickr = load_flickr_api()

    photos = list(flickr.walk_set(photoset_id=album_id))
    print(f"Found {len(photos)} photos in album\n")

    solved = 0
    skipped = 0
    failed = 0

    for i, photo in enumerate(photos, 1):
        photo_id = photo.attrib['id']
        title = photo.attrib.get('title', '(untitled)')
        print(f"[{i}/{len(photos)}] {title} ({photo_id})")

        if not args.force:
            status = get_astrometry_status(flickr, photo_id)
            if status == 'solved':
                print(f"    Already solved, skipping (use --force to re-solve)")
                skipped += 1
                continue
            elif status == 'failed':
                print(f"    Previously failed, skipping (use --force to retry)")
                skipped += 1
                continue

        cmd = [sys.executable, 'plate_solve.py', photo_id]

        if args.no_comment:
            cmd.insert(-1, '--no-comment')
        if args.no_note:
            cmd.insert(-1, '--no-note')

        if args.dry_run:
            print(f"    [DRY RUN] Would run: {' '.join(cmd)}")
            continue

        print(f"    Solving...")
        result = subprocess.run(cmd, cwd=sys.path[0] or '.')
        if result.returncode != 0:
            print(f"    WARNING: plate_solve.py exited with code {result.returncode}")
            failed += 1
        else:
            solved += 1
        print()

    print(f"\nDone! {solved} solved, {skipped} skipped, {failed} failed.")


if __name__ == "__main__":
    main()
