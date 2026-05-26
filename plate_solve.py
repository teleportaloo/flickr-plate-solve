#!/usr/bin/env python3
"""
Plate-solve a Flickr photo via astrometry.net, then tag and comment
the result back on the Flickr photo.

Takes a Flickr photo URL (or short flic.kr link or bare photo ID),
grabs the direct image URL via the Flickr API, submits it to
astrometry.net for plate solving, and posts the results back as
machine tags, a comment, and hover-over annotation notes.

Usage:
    python3 plate_solve.py https://www.flickr.com/photos/user/12345678
    python3 plate_solve.py https://flic.kr/p/2seqonc
    python3 plate_solve.py 55285840885
    python3 plate_solve.py --no-comment https://flic.kr/p/2seqonc
    python3 plate_solve.py --no-tag https://flic.kr/p/2seqonc
    python3 plate_solve.py --dry-run https://flic.kr/p/2seqonc
    python3 plate_solve.py --job 15931178 https://flic.kr/p/2seqonc
    python3 plate_solve.py --clear-notes --job 15931178 https://flic.kr/p/2seqonc
    python3 plate_solve.py --redo https://flic.kr/p/2seqonc

Requires:
    - Flickr API keys with write permission
      (get one at https://www.flickr.com/services/apps/create/apply/)
    - Astrometry.net API key
      (get one free at https://nova.astrometry.net — My Profile → API Key)
    - Keys in ./keys.json, ~/.config/plate-solve/keys.json, or env vars
      (see keys.json.example for format)
"""

import sys
import os
import re
import json
import time
import argparse
import urllib.request
import urllib.parse

from flickr_oauth import FlickrAPI

ASTROMETRY_BASE = "https://nova.astrometry.net/api"
SIMBAD_URL = "https://simbad.u-strasbg.fr/simbad/sim-id"


# --- Key loading ---

def load_keys():
    """Load API keys from keys.json or environment variables.

    Search order:
      1. ./keys.json (next to this script)
      2. ~/.config/plate-solve/keys.json
      3. Environment variables: FLICKR_KEY, FLICKR_SECRET, ASTROMETRY_KEY
    """
    # Search for keys.json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "keys.json"),
        os.path.expanduser("~/.config/plate-solve/keys.json"),
    ]

    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)

    # Fall back to environment variables
    flickr_key = os.environ.get("FLICKR_KEY")
    flickr_secret = os.environ.get("FLICKR_SECRET")
    astrometry_key = os.environ.get("ASTROMETRY_KEY")

    if flickr_key and flickr_secret:
        return {
            "flickr_keys": {
                "FLICKR_KEY": flickr_key,
                "FLICKR_SECRET": flickr_secret,
            },
            "astrometry_key": astrometry_key or "",
        }

    print("No API keys found. Create keys.json (see keys.json.example) or set env vars:", file=sys.stderr)
    print("  FLICKR_KEY, FLICKR_SECRET, ASTROMETRY_KEY", file=sys.stderr)
    sys.exit(1)


def load_flickr_api():
    """Initialize FlickrAPI from keys with write perms."""
    keys = load_keys()["flickr_keys"]
    flickr = FlickrAPI(keys["FLICKR_KEY"], keys["FLICKR_SECRET"])
    flickr.authenticate(perms='write')
    return flickr


def load_astrometry_key():
    """Load astrometry.net API key."""
    keys = load_keys()
    api_key = keys.get("astrometry_key")
    if not api_key:
        print("No astrometry_key found in keys.json.", file=sys.stderr)
        print("Get one at https://nova.astrometry.net (My Profile -> API Key)", file=sys.stderr)
        sys.exit(1)
    return api_key


# --- Flickr photo ID extraction ---

def get_photo_id(arg):
    """Extract photo ID from a Flickr URL, short URL, guest pass, or bare ID."""
    # Standard Flickr URL
    match = re.search(r"flickr\.com/photos/[^/]+/(\d+)", arg)
    if match:
        return match.group(1)

    # Bare numeric ID
    if arg.strip().isdigit():
        return arg.strip()

    # Short URL, guest pass, or photo.gne — resolve redirects
    if "flic.kr" in arg or "flickr.com/photo.gne" in arg or "flickr.com/gp/" in arg:
        # Use GET with redirect handler to follow the chain
        # (HEAD on guest pass URLs returns 403, but redirect happens first)
        class _RedirectCatcher(urllib.request.HTTPRedirectHandler):
            last_url = None
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                _RedirectCatcher.last_url = newurl
                return urllib.request.HTTPRedirectHandler.redirect_request(
                    self, req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(_RedirectCatcher)
        try:
            resp = opener.open(arg)
            final_url = resp.url
        except urllib.error.HTTPError:
            # Guest pass URLs 403 after redirect — but we got the redirect
            final_url = _RedirectCatcher.last_url or ""

        match = re.search(r"/photos/[^/]+/(\d+)", final_url)
        if match:
            return match.group(1)

    print(f"Can't parse photo ID from: {arg}", file=sys.stderr)
    sys.exit(1)


def get_direct_url(flickr, photo_id):
    """Get the direct static image URL from Flickr API."""
    resp = flickr.photos.getSizes(photo_id=photo_id)
    sizes = resp.findall('.//size')

    for label in ["Original", "Large 2048", "Large 1600", "Large"]:
        for s in sizes:
            if s.get('label') == label:
                return s.get('source'), s.get('label'), s.get('width'), s.get('height')

    # Fallback: last entry
    s = sizes[-1]
    return s.get('source'), s.get('label'), s.get('width'), s.get('height')


# --- Astrometry.net API ---

def astrometry_post(endpoint, data):
    """POST JSON to astrometry.net API."""
    url = f"{ASTROMETRY_BASE}/{endpoint}"
    form_data = urllib.parse.urlencode({
        'request-json': json.dumps(data)
    }).encode()
    req = urllib.request.Request(url, data=form_data)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def astrometry_get(endpoint):
    """GET from astrometry.net API."""
    url = f"{ASTROMETRY_BASE}/{endpoint}"
    resp = urllib.request.urlopen(url)
    return json.loads(resp.read())


def plate_solve(image_url, api_key, photo_arg="PHOTO"):
    """Submit image URL to astrometry.net and wait for results."""
    # Login
    print("Logging in to astrometry.net...")
    result = astrometry_post("login", {"apikey": api_key})
    if result.get("status") != "success":
        print(f"Login failed: {result}", file=sys.stderr)
        sys.exit(1)
    session = result["session"]

    # Submit URL
    print("Submitting image for plate solving...")
    result = astrometry_post("url_upload", {
        "session": session,
        "url": image_url,
        "publicly_visible": "n",
        "allow_modifications": "n",
        "allow_commercial_use": "n",
    })
    if result.get("status") != "success":
        print(f"Submission failed: {result}", file=sys.stderr)
        sys.exit(1)
    subid = result["subid"]
    print(f"Submission ID: {subid}")

    # Poll for job ID (up to 30 minutes — astrometry.net queue can be very slow)
    print("Waiting for job to start...", flush=True)
    job_id = None
    for i in range(60):
        time.sleep(30)
        status = astrometry_get(f"submissions/{subid}")
        jobs = status.get("jobs", [])
        if jobs and jobs[0] is not None:
            job_id = jobs[0]
            break
        elapsed = (i + 1) * 30
        print(f"  {elapsed // 60}m {elapsed % 60:02d}s — still queued", flush=True)
    print()

    if not job_id:
        print("Timed out waiting for job to start (30 mins).", file=sys.stderr)
        print(f"Check manually: https://nova.astrometry.net/status/{subid}", file=sys.stderr)
        print(f"\nOnce a job ID appears, re-run with:", file=sys.stderr)
        print(f"  python3 plate_solve.py {photo_arg} --job <JOB_ID>", file=sys.stderr)
        sys.exit(1)

    # Poll for solve (up to 30 minutes once job starts)
    print(f"Job {job_id} solving...", flush=True)
    for i in range(60):
        time.sleep(30)
        status = astrometry_get(f"jobs/{job_id}")
        if status.get("status") == "success":
            print("Solved!")
            break
        elif status.get("status") == "failure":
            print("Failed — astrometry.net could not solve this image.", file=sys.stderr)
            # Return empty results so caller can tag the failure
            calibration = {}
            info = {"objects_in_field": []}
            return job_id, calibration, info
        elapsed = (i + 1) * 30
        print(f"  {elapsed // 60}m {elapsed % 60:02d}s — solving", flush=True)
    else:
        print("Timed out waiting for solve (30 mins).", file=sys.stderr)
        print(f"\nRe-run with this job when it finishes:", file=sys.stderr)
        print(f"  python3 plate_solve.py {photo_arg} --job {job_id}", file=sys.stderr)
        sys.exit(1)

    # Get results
    calibration = astrometry_get(f"jobs/{job_id}/calibration/")
    info = astrometry_get(f"jobs/{job_id}/info/")

    return job_id, calibration, info


# --- Flickr tagging and commenting ---

def ra_to_hms(ra_deg):
    """Convert RA in degrees to hours:min:sec string."""
    ra_hr = ra_deg / 15.0
    h = int(ra_hr)
    m = int((ra_hr - h) * 60)
    s = (ra_hr - h - m / 60.0) * 3600
    return f"{h:02d}h {m:02d}m {s:05.2f}s"


def dec_to_dms(dec_deg):
    """Convert Dec in degrees to deg:min:sec string."""
    sign = "+" if dec_deg >= 0 else "-"
    dec_abs = abs(dec_deg)
    d = int(dec_abs)
    m = int((dec_abs - d) * 60)
    s = (dec_abs - d - m / 60.0) * 3600
    return f"{sign}{d:02d}d {m:02d}m {s:05.2f}s"


def build_machine_tags(calibration, info):
    """Build Flickr machine tags from plate solve results."""
    tags = []

    # Astrometry namespace tags (matches the original bot's format)
    ra = calibration.get('ra')
    dec = calibration.get('dec')
    try:
        if ra is not None:
            tags.append(f"astrometry:RA={float(ra):.6f}")
    except (ValueError, TypeError):
        pass
    try:
        if dec is not None:
            tags.append(f"astrometry:Dec={float(dec):.6f}")
    except (ValueError, TypeError):
        pass

    pixscale = calibration.get('pixscale')
    try:
        if pixscale is not None:
            tags.append(f"astrometry:pixscale={float(pixscale):.4f}")
    except (ValueError, TypeError):
        pass

    orientation = calibration.get('orientation')
    try:
        if orientation is not None:
            tags.append(f"astrometry:orientation={float(orientation):.2f}")
    except (ValueError, TypeError):
        pass

    radius = calibration.get('radius')
    try:
        if radius is not None:
            tags.append(f"astrometry:fieldradius={float(radius):.4f}")
    except (ValueError, TypeError):
        pass

    # Object tags
    for obj in info.get("objects_in_field", []):
        safe_obj = obj.replace(" ", "_")
        tags.append(f"astrometry:object={safe_obj}")

    tags.append("astrometry:status=solved")

    return tags


def build_comment(photo_id, calibration, info, job_id):
    """Build a human-readable comment with plate solve results (ASCII safe)."""
    ra = calibration.get('ra', 0)
    dec = calibration.get('dec', 0)
    pixscale = calibration.get('pixscale', 0)
    orientation = calibration.get('orientation', 0)
    radius = calibration.get('radius', 0)
    objects = info.get("objects_in_field", [])

    # Ensure numeric types for formatting
    try:
        ra = float(ra)
        dec = float(dec)
        pixscale = float(pixscale)
        orientation = float(orientation)
        radius = float(radius)
    except (ValueError, TypeError):
        ra = dec = pixscale = orientation = radius = 0.0

    lines = []
    lines.append("Plate Solve by astrometry.net")
    lines.append("")
    lines.append(f"Center (RA): {ra_to_hms(ra)}  ({ra:.4f} deg)")
    lines.append(f"Center (Dec): {dec_to_dms(dec)}  ({dec:.4f} deg)")
    lines.append(f"Pixel scale: {pixscale:.3f} arcsec/pixel")
    lines.append(f"Orientation: {orientation:.2f} deg")
    lines.append(f"Field radius: {radius:.4f} deg ({radius * 60:.1f} arcmin)")

    if objects:
        lines.append("")
        lines.append(f"Objects in field ({len(objects)}):")
        for obj in objects:
            lines.append(f"  - {obj}")

    lines.append("")
    lines.append(f"Annotated: https://nova.astrometry.net/annotated_display/{job_id}")

    return "\n".join(lines)


def add_tags(flickr, photo_id, tags, dry_run=False):
    """Add machine tags to a Flickr photo, one at a time."""
    if dry_run:
        print(f"\n[DRY RUN] Would add tags to photo {photo_id}:")
        for t in tags:
            print(f"  {t}")
        return

    print(f"\nAdding {len(tags)} machine tags...")
    for t in tags:
        flickr.oauth.call_method_post('flickr.photos.addTags',
                                      photo_id=photo_id,
                                      tags=t)
        print(f"  + {t}")
    print("Tags added.")


def add_comment(flickr, photo_id, comment_text, dry_run=False):
    """Add a comment to a Flickr photo."""
    if dry_run:
        print(f"\n[DRY RUN] Would add comment to photo {photo_id}:")
        print(comment_text)
        return

    print("Adding comment...")
    flickr.oauth.call_method_post('flickr.photos.comments.addComment',
                                  photo_id=photo_id,
                                  comment_text=comment_text)
    print("Comment added.")


# --- SIMBAD name resolution ---

# SIMBAD object type abbreviations → friendly names
_SIMBAD_OTYPES = {
    'G': 'Galaxy', 'GiC': 'Galaxy in Cluster', 'GiG': 'Galaxy in Group',
    'GiP': 'Galaxy in Pair', 'BiC': 'Brightest Galaxy in Cluster',
    'IG': 'Interacting Galaxy', 'PaG': 'Pair of Galaxies',
    'GrG': 'Group of Galaxies', 'ClG': 'Galaxy Cluster',
    'AGN': 'Active Galaxy', 'SyG': 'Seyfert Galaxy',
    'Sy1': 'Seyfert 1 Galaxy', 'Sy2': 'Seyfert 2 Galaxy',
    'LIN': 'LINER Galaxy', 'SBG': 'Starburst Galaxy',
    'bCG': 'Blue Compact Galaxy', 'EmG': 'Emission-line Galaxy',
    'LSB': 'Low Surface Brightness Galaxy', 'HII': 'HII Region',
    'PN': 'Planetary Nebula', 'RNe': 'Reflection Nebula',
    'SNR': 'Supernova Remnant', 'SR?': 'Supernova Remnant Candidate',
    'ISM': 'Interstellar Medium', 'DNe': 'Dark Nebula',
    'EmO': 'Emission Object', 'Cld': 'Cloud',
    'GNe': 'Galactic Nebula', 'BNe': 'Bright Nebula',
    'MoC': 'Molecular Cloud', 'HVC': 'High-velocity Cloud',
    'SFR': 'Star Forming Region',
    'GlC': 'Globular Cluster', 'OpC': 'Open Cluster',
    'Cl*': 'Star Cluster', 'As*': 'Stellar Association',
    'St*': 'Stellar Stream', 'MGr': 'Moving Group',
    '*': 'Star', '**': 'Double Star', '*iC': 'Star in Cluster',
    '*iN': 'Star in Nebula', '*iA': 'Star in Association',
    'V*': 'Variable Star', 'Ce*': 'Cepheid', 'RR*': 'RR Lyrae',
    'Mi*': 'Mira Variable', 'Pu*': 'Pulsating Star',
    'Ec*': 'Eclipsing Binary', 'SB*': 'Spectroscopic Binary',
    'WD*': 'White Dwarf', 'NS*': 'Neutron Star', 'BH*': 'Black Hole',
    'WR*': 'Wolf-Rayet Star', 'Be*': 'Be Star',
    'RG*': 'Red Giant', 'SG*': 'Supergiant',
    'HB*': 'Horizontal Branch Star', 'HS*': 'Hot Subdwarf',
    'LP*': 'Long-period Variable', 'PM*': 'High Proper Motion Star',
    'QSO': 'Quasar', 'BLL': 'BL Lac', 'Bla': 'Blazar',
    'Rad': 'Radio Source', 'mR': 'Metric Radio Source',
    'cm': 'cm Radio Source', 'mm': 'mm Radio Source',
    'smm': 'sub-mm Source', 'HI': 'HI Source',
    'rG': 'Radio Galaxy', 'X': 'X-ray Source',
    'gam': 'Gamma-ray Source', 'Psr': 'Pulsar',
    'No*': 'Nova', 'SN*': 'Supernova', 'Su*': 'Supergiant',
    'HighPM*': 'High Proper Motion Star',
    'Seyfert2': 'Seyfert 2 Galaxy', 'Seyfert1': 'Seyfert 1 Galaxy',
    'Seyfert_2': 'Seyfert 2 Galaxy', 'Seyfert_1': 'Seyfert 1 Galaxy',
    'C*': 'Carbon Star', 'S*': 'S Star', 'OH*': 'OH/IR Star',
    'TT*': 'T Tauri Star', 'Ae*': 'Herbig Ae/Be Star',
    'Or*': 'Orion Variable', 'FU*': 'FU Ori Variable',
    'RS*': 'RS CVn Variable', 'BY*': 'BY Dra Variable',
    'a2*': 'Alpha2 CVn Variable', 'El*': 'Ellipsoidal Variable',
    'blu': 'Blue Object', 'err': 'Not an Object',
    'IR': 'Infrared Source', 'UV': 'UV Source',
    'Neb': 'Nebula', 'CGb': 'Cometary Globule',
    'mul': 'Composite Object', 'reg': 'Region',
    'SCG': 'Supercluster of Galaxies', 'vid': 'Void',
    'HIIReg': 'HII Region', 'GlobCluster': 'Globular Cluster',
    'GtowardsCl': 'Galaxy towards Cluster', 'Radio': 'Radio Source',
    'OpenCluster': 'Open Cluster', 'DkNeb': 'Dark Nebula',
    'RfNeb': 'Reflection Nebula', 'EmNeb': 'Emission Nebula',
    'PlNeb': 'Planetary Nebula', 'SNRem': 'Supernova Remnant',
    'Nova': 'Nova', 'Supernova': 'Supernova',
    'Galaxy': 'Galaxy', 'Nebula': 'Nebula',
    'StarCluster': 'Star Cluster', 'Star': 'Star',
}


def _friendly_otype(otype):
    """Convert SIMBAD object type code to a friendly name."""
    if not otype:
        return None
    otype = otype.strip()
    return _SIMBAD_OTYPES.get(otype, otype)


def simbad_lookup(object_name, verbose=True):
    """Look up common names, magnitude, and type for an astronomical object.

    Returns a dict with:
      'names': list of human-friendly names (common names and Messier numbers)
      'mag': visual magnitude (float) or None
      'type': friendly object type string or None
    """
    result = {'names': [], 'mag': None, 'type': None}
    try:
        import xml.etree.ElementTree as ET
        query = urllib.parse.urlencode({
            'Ident': object_name,
            'output.format': 'votable',
            'output.params': 'main_id,ids,otype,flux(V),flux(B),flux(G)',
        })
        url = f"{SIMBAD_URL}?{query}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read().decode()

        root = ET.fromstring(data)
        ns = {'v': 'http://www.ivoa.net/xml/VOTable/v1.2'}

        # Figure out which column is which from FIELD definitions
        fields = root.findall('.//v:FIELD', ns)
        col_map = {}
        for i, f in enumerate(fields):
            name = f.get('name', '').upper()
            if 'IDS' in name:
                col_map['ids'] = i
            elif name == 'OTYPE':
                col_map['otype'] = i
            elif name == 'FLUX_V' or name == 'FLUX(V)':
                col_map['flux_v'] = i
            elif name == 'FLUX_B' or name == 'FLUX(B)':
                col_map['flux_b'] = i
            elif name == 'FLUX_G' or name == 'FLUX(G)':
                col_map['flux_g'] = i

        rows = root.findall('.//v:TR', ns)
        if not rows:
            if verbose:
                print(f"    {object_name}: not found in SIMBAD")
            return result

        for tr in rows:
            tds = tr.findall('v:TD', ns)

            # Parse identifiers
            ids_idx = col_map.get('ids', 1)
            if ids_idx < len(tds) and tds[ids_idx].text:
                ids = [n.strip() for n in tds[ids_idx].text.split('|')]
            else:
                if verbose:
                    print(f"    {object_name}: no identifiers in SIMBAD")
                continue

            # Parse object type
            otype_idx = col_map.get('otype')
            if otype_idx is not None and otype_idx < len(tds) and tds[otype_idx].text:
                result['type'] = _friendly_otype(tds[otype_idx].text)

            # Parse magnitude: prefer V-band, fall back to B, then Gaia G
            for flux_key in ('flux_v', 'flux_b', 'flux_g'):
                flux_idx = col_map.get(flux_key)
                if flux_idx is not None and flux_idx < len(tds) and tds[flux_idx].text:
                    try:
                        result['mag'] = float(tds[flux_idx].text)
                        break
                    except ValueError:
                        continue

            # Normalise whitespace in all identifiers (SIMBAD has "M  81" etc.)
            ids = [' '.join(n.split()) for n in ids]

            # Prefer common names (NAME xxx)
            named = [n.replace('NAME ', '') for n in ids if n.startswith('NAME ')]
            # Fall back to Messier number
            messier = [n for n in ids
                       if n.startswith('M ') and len(n) < 7]

            # Combine: all common names + any Messier not already covered
            names = list(named)
            for m in messier:
                if m not in names:
                    names.append(m)
            result['names'] = names

            if verbose:
                name_entries = [n for n in ids if n.startswith('NAME ')]
                extras = []
                if result['type']:
                    extras.append(result['type'])
                if result['mag'] is not None:
                    extras.append(f"mag {result['mag']:.1f}")
                extra_str = f"  [{', '.join(extras)}]" if extras else ""
                if names:
                    print(f"    {object_name} -> {names}{extra_str}  (from: {name_entries})")
                else:
                    print(f"    {object_name}: no common name  ({len(ids)} ids){extra_str}")

            return result
    except Exception as e:
        if verbose:
            print(f"    {object_name}: SIMBAD error ({e})")
    return result


# --- Flickr notes ---

def clear_notes(flickr, photo_id, dry_run=False):
    """Remove all existing notes from a Flickr photo."""
    resp = flickr.oauth.call_method('flickr.photos.getInfo', photo_id=photo_id)
    notes = resp.findall('.//note')
    if not notes:
        print("\nNo existing notes to remove.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would remove {len(notes)} notes:")
        for n in notes:
            print(f"  - {n.text}")
        return

    print(f"\nRemoving {len(notes)} existing notes...")
    for n in notes:
        flickr.oauth.call_method_post('flickr.photos.notes.delete',
                                      note_id=n.get('id'))
        print(f"  - {n.text}")
    print("Notes cleared.")


def get_job_from_comments(flickr, photo_id):
    """Check a photo's comments for an astrometry.net job link.

    Returns the job ID (int) if found, or None.
    """
    try:
        resp = flickr.oauth.call_method('flickr.photos.comments.getList',
                                        photo_id=photo_id)
        comments = resp.findall('.//comment')
        for comment in comments:
            text = comment.text or ''
            m = re.search(r'nova\.astrometry\.net/annotated_display/(\d+)', text)
            if m:
                return int(m.group(1))
    except Exception as e:
        print(f"  Error reading comments: {e}")
    return None


REPO_URL = "https://github.com/teleportaloo/flickr-plate-solve"
REPO_COMMENT = f"Generated by flickr-plate-solve: {REPO_URL}"


def add_repo_comment(flickr, photo_id, dry_run=False):
    """Add a comment linking to the GitHub repo, if not already present."""
    try:
        resp = flickr.oauth.call_method('flickr.photos.comments.getList',
                                        photo_id=photo_id)
        comments = resp.findall('.//comment')
        for comment in comments:
            text = comment.text or ''
            if 'flickr-plate-solve' in text:
                return  # already there
    except Exception:
        pass  # no comments yet, that's fine

    if dry_run:
        print(f"[DRY RUN] Would add repo comment")
        return

    flickr.oauth.call_method_post('flickr.photos.comments.addComment',
                                   photo_id=photo_id,
                                   comment_text=REPO_COMMENT)
    print("Added repo link comment.")


def get_image_dimensions(flickr, photo_id):
    """Get the Original and Medium (500px) image dimensions."""
    resp = flickr.photos.getSizes(photo_id=photo_id)
    sizes = resp.findall('.//size')
    original = None
    medium = None
    for s in sizes:
        if s.get('label') == 'Original':
            original = (int(s.get('width')), int(s.get('height')))
        elif s.get('label') == 'Medium':
            medium = (int(s.get('width')), int(s.get('height')))
    return original, medium


# Flickr allows at most 100 notes per photo
MAX_NOTES = 100

# Greek letter prefixes used in Bayer star designations
_GREEK = ('α', 'β', 'γ', 'δ', 'ε', 'ζ',
          'η', 'θ', 'ι', 'κ', 'λ', 'μ',
          'ν', 'ξ', 'ο', 'π', 'ρ', 'σ',
          'τ', 'υ', 'φ', 'χ', 'ψ', 'ω')


def _annotation_priority(annotation):
    """Return a priority score for an annotation (lower = more important).

    Priority tiers:
      0 - Deep-sky objects (NGC, IC, Messier, Abell, Sharpless, etc.)
      1 - Named stars (Arcturus, Polaris, etc.)
      2 - Greek-letter / Bayer stars (alpha UMa, beta Boo)
      3 - Flamsteed numbered stars (27 Com, 64 Leo)
      4 - Star catalogue entries (HD, TYC, SAO, etc.)
      5 - Unnamed / unknown
    """
    names = annotation.get("names", [])
    if not names:
        return 5

    name = names[0]

    # Tier 0: deep-sky catalogues
    DSO_PREFIXES = ('NGC', 'IC', 'M ', 'Messier', 'Abell', 'Sh2-',
                    'LBN', 'LDN', 'Barnard', 'Cr ', 'Mel ', 'Ced',
                    'PGC', 'UGC', 'vdB')
    for prefix in DSO_PREFIXES:
        if name.startswith(prefix):
            return 0

    # Tier 4: star catalogues (check before named stars — some have names appended)
    CATALOGUE_PREFIXES = ('HD', 'TYC', 'SAO', 'HIP', 'UCAC', '2MASS',
                          'GSC', 'USNO', 'PPM', 'HR ')
    for prefix in CATALOGUE_PREFIXES:
        if name.startswith(prefix):
            return 4

    # Tier 1: has a proper name (multi-word with a capitalized name, or standalone name)
    # e.g. ['alpha Boo / 16 Boo', 'Arcturus'] or ['Polaris / Alrucaba / ...']
    if len(names) > 1:
        # Second entry is often the proper name
        return 1
    # Single name that's a proper name (not a number, not Greek+constellation)
    if any(name.startswith(g) for g in _GREEK):
        # Check if it also has a proper name embedded
        if ',' in name:
            return 1
        return 2

    # Tier 3: Flamsteed numbered stars (e.g. "27 Com", "64 Leo")
    parts = name.split()
    if len(parts) >= 2 and parts[0].isdigit():
        return 3

    # Tier 1: anything else with a letter name (proper name like "Tonatiuh", "La Superba")
    if name[0].isalpha() and not name[0].isdigit():
        return 1

    return 5


def add_notes(flickr, photo_id, job_id, orig_w, orig_h, medium_w, medium_h, dry_run=False):
    """Add Flickr photo notes from astrometry.net annotations.

    Flickr notes use pixel coordinates based on the 500px Medium size.
    We scale from the original image coordinates (used by astrometry.net)
    to Medium size. When there are more than MAX_NOTES annotations,
    prioritises deep-sky objects and named stars over catalogue entries.
    """
    annotations = astrometry_get(f"jobs/{job_id}/annotations/")
    objects = annotations.get("annotations", [])

    if len(objects) <= MAX_NOTES:
        # Few enough to keep them all
        filtered = objects
        print(f"  {len(objects)} annotations (within Flickr's {MAX_NOTES} note limit)")
    else:
        # Too many — prioritise by interestingness
        scored = sorted(objects, key=_annotation_priority)
        filtered = scored[:MAX_NOTES]
        skipped = len(objects) - MAX_NOTES
        # Show what we kept by tier
        kept_tiers = {}
        for a in filtered:
            p = _annotation_priority(a)
            tier_names = {0: 'deep-sky', 1: 'named stars', 2: 'Bayer stars',
                          3: 'Flamsteed stars', 4: 'catalogue', 5: 'other'}
            tier = tier_names.get(p, 'other')
            kept_tiers[tier] = kept_tiers.get(tier, 0) + 1
        tier_summary = ', '.join(f'{v} {k}' for k, v in kept_tiers.items())
        print(f"  {len(objects)} annotations, keeping top {MAX_NOTES} ({tier_summary}), skipped {skipped}")

    # Look up common names, type, and magnitude via SIMBAD
    if filtered:
        print("  Looking up object names via SIMBAD...")
        for a in filtered:
            if a.get("names"):
                info = simbad_lookup(a["names"][0])
                for name in info['names']:
                    if name not in a["names"]:
                        a["names"].append(name)
                if info['mag'] is not None:
                    a['_mag'] = info['mag']
                if info['type']:
                    a['_type'] = info['type']

    if not filtered:
        print("\nNo notable objects to annotate.")
        return

    # Scale factor from original to Medium 500px
    scale_x = medium_w / orig_w
    scale_y = medium_h / orig_h

    # Minimum note sizes (pixels in Medium coords)
    min_size_dso = 40   # deep-sky objects — bigger boxes
    min_size_star = 10  # stars — tiny markers

    if dry_run:
        print(f"\n[DRY RUN] Would add {len(filtered)} notes to photo {photo_id}:")
        for a in filtered:
            parts = [", ".join(a.get("names", ["Unknown"]))]
            extras = []
            if a.get('_type'):
                extras.append(a['_type'])
            if a.get('_mag') is not None:
                extras.append(f"mag {a['_mag']:.1f}")
            if extras:
                parts.append(f"({', '.join(extras)})")
            print(f"  {' '.join(parts)}")
        return

    print(f"\nAdding {len(filtered)} annotations...")
    added = 0
    for a in filtered:
        parts = [", ".join(a.get("names", ["Unknown"]))]
        extras = []
        if a.get('_type'):
            extras.append(a['_type'])
        if a.get('_mag') is not None:
            extras.append(f"mag {a['_mag']:.1f}")
        if extras:
            parts.append(f"({', '.join(extras)})")
        label = " ".join(parts)
        px = a.get("pixelx", 0)
        py = a.get("pixely", 0)
        radius = a.get("radius", 0)

        # Stars (radius 0) get smaller boxes than deep-sky objects
        is_dso = _annotation_priority(a) == 0
        min_size = min_size_dso if is_dso else min_size_star

        # Scale to Medium image coordinates
        sx = px * scale_x
        sy = py * scale_y
        sr = max(radius * scale_x, min_size / 2)

        # Convert center+radius to top-left + width/height
        # Cap note size to image dimensions
        note_w = min(max(int(sr * 2), min_size), medium_w)
        note_h = min(max(int(sr * 2), min_size), medium_h)
        note_x = max(int(sx - sr), 0)
        note_y = max(int(sy - sr), 0)

        # Clamp so note stays within image bounds
        note_x = min(note_x, medium_w - note_w)
        note_y = min(note_y, medium_h - note_h)

        try:
            flickr.oauth.call_method_post('flickr.photos.notes.add',
                                          photo_id=photo_id,
                                          note_x=str(note_x),
                                          note_y=str(note_y),
                                          note_w=str(note_w),
                                          note_h=str(note_h),
                                          note_text=label)
            added += 1
            print(f"  + {label} ({note_w}x{note_h} at {note_x},{note_y})")
        except Exception as e:
            if "Maximum number of notes" in str(e):
                print(f"\n  Flickr note limit reached after {added} notes.")
                break
            elif "exceed photo dimensions" in str(e):
                print(f"  ! {label} — skipped (exceeds image bounds)")
                continue
            raise
    print("Notes added.")


# --- Output ---

def _fmt(value, fmt_str):
    """Format a numeric value, returning 'N/A' for non-numeric types."""
    try:
        return format(float(value), fmt_str)
    except (ValueError, TypeError):
        return 'N/A'


def calibration_valid(calibration):
    """Check if calibration data represents a real solve (not all zeros/empty)."""
    if not calibration or 'ra' not in calibration:
        return False
    try:
        ra = float(calibration.get('ra', 0))
        dec = float(calibration.get('dec', 0))
        # A solve at exactly RA=0, Dec=0 with no pixel scale is almost certainly failed
        if ra == 0 and dec == 0 and not calibration.get('pixscale'):
            return False
    except (ValueError, TypeError):
        return False
    return True


def print_results(job_id, calibration, info):
    """Print plate solve results to console."""
    print(f"\n{'='*50}")
    print("PLATE SOLVE RESULTS")
    print(f"{'='*50}")

    if not calibration_valid(calibration):
        print("  Solve FAILED — no valid calibration data.")
        print(f"  Check job: https://nova.astrometry.net/status/{job_id}")
        return

    ra = calibration.get('ra', 0)
    dec = calibration.get('dec', 0)

    print(f"  RA:           {ra_to_hms(ra)}  ({_fmt(ra, '.4f')}deg)")
    print(f"  Dec:          {dec_to_dms(dec)}  ({_fmt(dec, '.4f')}deg)")
    print(f"  Orientation:  {_fmt(calibration.get('orientation'), '.2f')}deg")
    print(f"  Pixel scale:  {_fmt(calibration.get('pixscale'), '.3f')} arcsec/pixel")
    print(f"  Field radius: {_fmt(calibration.get('radius'), '.4f')}deg")

    objects = info.get("objects_in_field", [])
    if objects:
        print(f"\nObjects in field:")
        for obj in objects:
            print(f"  - {obj}")

    print(f"\nAnnotated image:")
    print(f"  https://nova.astrometry.net/annotated_display/{job_id}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(
        description="Plate-solve a Flickr photo and post results back as tags/comment/notes.",
        epilog="Requires Flickr write API key and astrometry.net API key."
    )
    parser.add_argument('photo', help='Flickr URL, flic.kr short link, guest pass URL, or photo ID')
    parser.add_argument('--no-comment', action='store_true',
                        help='Skip adding a comment to the photo')
    parser.add_argument('--no-tag', action='store_true',
                        help='Skip adding machine tags to the photo')
    parser.add_argument('--no-note', action='store_true',
                        help='Skip adding annotation notes to the photo')
    parser.add_argument('--clear-notes', action='store_true',
                        help='Remove existing notes before adding new ones')
    parser.add_argument('--dry-run', action='store_true',
                        help='Solve but don\'t write anything to Flickr')
    parser.add_argument('--job', type=int, metavar='JOB_ID',
                        help='Reuse a previous astrometry.net job instead of re-solving')
    parser.add_argument('--redo', action='store_true',
                        help='Re-do notes only: read job from existing comment, '
                             'clear old notes, skip comment and tags')
    args = parser.parse_args()

    if args.redo:
        args.clear_notes = True
        args.no_comment = True
        args.no_tag = True

    # Get Flickr photo ID
    photo_id = get_photo_id(args.photo)
    flickr = load_flickr_api()

    if args.redo and not args.job:
        # Look up job ID from existing plate-solve comment
        print(f"Flickr photo {photo_id}, looking for existing solve...")
        job_id = get_job_from_comments(flickr, photo_id)
        if not job_id:
            print("No plate-solve comment found — nothing to redo.", file=sys.stderr)
            sys.exit(1)
        args.job = job_id

    if args.job:
        # Reuse a previous astrometry.net job
        job_id = args.job
        print(f"Flickr photo {photo_id}, reusing astrometry.net job {job_id}")
        calibration = astrometry_get(f"jobs/{job_id}/calibration/")
        info = astrometry_get(f"jobs/{job_id}/info/")
    else:
        # Get direct URL and plate solve
        image_url, label, w, h = get_direct_url(flickr, photo_id)
        print(f"Flickr photo {photo_id}: {label} ({w}x{h})")
        print(f"Direct URL: {image_url}")
        api_key = load_astrometry_key()
        job_id, calibration, info = plate_solve(image_url, api_key, photo_arg=args.photo)

    print_results(job_id, calibration, info)

    # Clear notes even if the solve failed (user may want a clean slate)
    if args.clear_notes:
        clear_notes(flickr, photo_id, dry_run=args.dry_run)

    # Only post results if the solve actually succeeded
    if not calibration_valid(calibration):
        print("\nSkipping tags/comment/notes — no valid solve data.")
        if not args.no_tag and not args.dry_run:
            print("Tagging photo as failed...")
            flickr.oauth.call_method_post('flickr.photos.addTags',
                                          photo_id=photo_id,
                                          tags='astrometry:status=failed')
            print("  + astrometry:status=failed")
    else:
        if not args.no_tag:
            tags = build_machine_tags(calibration, info)
            add_tags(flickr, photo_id, tags, dry_run=args.dry_run)

        if not args.no_comment:
            comment = build_comment(photo_id, calibration, info, job_id)
            add_comment(flickr, photo_id, comment, dry_run=args.dry_run)

        if not args.no_note:
            original, medium = get_image_dimensions(flickr, photo_id)
            if original and medium:
                add_notes(flickr, photo_id, job_id,
                          original[0], original[1],
                          medium[0], medium[1],
                          dry_run=args.dry_run)
            else:
                print("\nCouldn't get image dimensions for notes.")

    # Add repo link comment if not already present
    if calibration_valid(calibration):
        add_repo_comment(flickr, photo_id, dry_run=args.dry_run)

    if args.dry_run:
        print("\n[DRY RUN] No changes made to Flickr.")
    else:
        print(f"\nDone! Check: https://www.flickr.com/photo.gne?id={photo_id}")


if __name__ == "__main__":
    main()
