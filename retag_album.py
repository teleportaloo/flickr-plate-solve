#!/usr/bin/env python3
"""
Re-tag all plate-solved photos in a Flickr album.

Walks through every photo in an album, finds those with an existing
plate-solve comment (containing an astrometry.net job link), and
re-runs the plate solver with --clear-notes to redo the annotations
using the original job (no re-solving needed).

Usage:
    python3 retag_album.py https://www.flickr.com/photos/major_clanger/albums/72177720326735849/
    python3 retag_album.py 72177720326735849
    python3 retag_album.py --dry-run https://www.flickr.com/photos/major_clanger/albums/72177720326735849/

Requires the same keys.json as plate_solve.py.
"""

import sys
import re
import argparse
import subprocess

from plate_solve import load_flickr_api


def get_album_id(arg):
    """Extract album/photoset ID from a Flickr URL or bare ID."""
    # URL like https://www.flickr.com/photos/user/albums/72177720326735849/
    m = re.search(r'/albums/(\d+)', arg)
    if m:
        return m.group(1)
    # Bare numeric ID
    if arg.strip().isdigit():
        return arg.strip()
    print(f"Could not extract album ID from: {arg}", file=sys.stderr)
    sys.exit(1)


def get_job_from_comments(flickr, photo_id):
    """Check a photo's comments for an astrometry.net job link.

    Returns the job ID (int) if found, or None.
    """
    try:
        resp = flickr.photos.comments.getList(photo_id=photo_id)
        comments = resp.findall('.//comment')
        for comment in comments:
            text = comment.text or ''
            m = re.search(r'nova\.astrometry\.net/annotated_display/(\d+)', text)
            if m:
                return int(m.group(1))
    except Exception as e:
        print(f"    Error reading comments: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Re-tag all plate-solved photos in a Flickr album')
    parser.add_argument('album', help='Flickr album URL or photoset ID')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without changing anything')
    parser.add_argument('--no-comment', action='store_true',
                        help='Skip re-adding the comment (keep existing)')
    parser.add_argument('--no-tag', action='store_true',
                        help='Skip re-adding machine tags (keep existing)')
    args = parser.parse_args()

    album_id = get_album_id(args.album)
    print(f"Album: {album_id}")

    flickr = load_flickr_api()

    # Walk all photos in the album
    photos = list(flickr.walk_set(photoset_id=album_id))
    print(f"Found {len(photos)} photos in album\n")

    solved = 0
    skipped = 0

    for i, photo in enumerate(photos, 1):
        photo_id = photo.attrib['id']
        title = photo.attrib.get('title', '(untitled)')
        print(f"[{i}/{len(photos)}] {title} ({photo_id})")

        job_id = get_job_from_comments(flickr, photo_id)
        if not job_id:
            print(f"    No plate-solve comment found, skipping")
            skipped += 1
            continue

        print(f"    Found job {job_id}")
        solved += 1

        # Build plate_solve.py command
        cmd = [
            sys.executable, 'plate_solve.py',
            '--clear-notes',
            '--no-comment',  # always skip comment — it already exists
            '--job', str(job_id),
            photo_id,
        ]

        if args.no_tag:
            cmd.insert(-1, '--no-tag')

        if args.dry_run:
            print(f"    [DRY RUN] Would run: {' '.join(cmd)}")
            continue

        print(f"    Re-tagging...")
        result = subprocess.run(cmd, cwd=sys.path[0] or '.')
        if result.returncode != 0:
            print(f"    WARNING: plate_solve.py exited with code {result.returncode}")
        print()

    print(f"\nDone! {solved} plate-solved photos, {skipped} skipped.")


if __name__ == "__main__":
    main()
