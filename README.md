# flickr-plate-solve

Plate-solve Flickr astrophotography images using [astrometry.net](https://nova.astrometry.net) and annotate them with machine tags, comments, and hover-over notes identifying stars and deep-sky objects.

## What it does

1. Takes a Flickr photo URL (or short link, guest pass URL, or bare photo ID)
2. Submits the image to astrometry.net for plate solving
3. Posts results back to Flickr as:
   - **Machine tags** — `astrometry:ra`, `astrometry:dec`, `astrometry:orientation`, etc.
   - **Comment** — human-readable calibration summary
   - **Notes** — hover-over annotations on the image identifying stars and deep-sky objects (with SIMBAD name resolution)

## Installation

```bash
mkdir PlateSolve && cd PlateSolve
python3 -m venv venv
source venv/bin/activate
pip install git+https://github.com/teleportaloo/flickr-plate-solve.git
```

Or clone and install in development mode (edits to `plate_solve.py` take effect immediately):

```bash
mkdir PlateSolve && cd PlateSolve
python3 -m venv venv
source venv/bin/activate
git clone https://github.com/teleportaloo/flickr-plate-solve.git
cd flickr-plate-solve
pip install -e .
```

## API keys

You need three keys:

| Key | Where to get it |
|-----|----------------|
| Flickr API Key | [flickr.com/services/apps/create/apply](https://www.flickr.com/services/apps/create/apply/) |
| Flickr API Secret | Same page as above |
| Astrometry.net API Key | [nova.astrometry.net](https://nova.astrometry.net) — My Profile → API Key (free) |

### Key configuration

Copy the example file and fill in your keys:

```bash
cp keys.json.example keys.json
```

Or place it in your config directory:

```bash
mkdir -p ~/.config/plate-solve
cp keys.json.example ~/.config/plate-solve/keys.json
```

Or use environment variables:

```bash
export FLICKR_KEY="your_flickr_api_key"
export FLICKR_SECRET="your_flickr_api_secret"
export ASTROMETRY_KEY="your_astrometry_net_api_key"
```

Search order: `./keys.json` → `~/.config/plate-solve/keys.json` → environment variables.

## Usage

```bash
# Plate-solve by Flickr URL
python3 plate_solve.py https://www.flickr.com/photos/user/12345678

# Short link
python3 plate_solve.py https://flic.kr/p/2seqonc

# Bare photo ID
python3 plate_solve.py 55285840885

# Guest pass URL (for private photos)
python3 plate_solve.py https://www.flickr.com/gp/major_clanger/S309653WCB

# Re-use a previous astrometry.net job (skips re-solving)
python3 plate_solve.py --job 15931178 https://flic.kr/p/2seqonc

# Dry run — solve but don't write anything to Flickr
python3 plate_solve.py --dry-run https://flic.kr/p/2seqonc

# Skip specific outputs
python3 plate_solve.py --no-comment https://flic.kr/p/2seqonc
python3 plate_solve.py --no-tag https://flic.kr/p/2seqonc
python3 plate_solve.py --no-note https://flic.kr/p/2seqonc

# Clear existing notes before adding new ones
python3 plate_solve.py --clear-notes https://flic.kr/p/2seqonc
```

If installed via pip, you can also use the console command:

```bash
flickr-plate-solve https://www.flickr.com/photos/user/12345678
```

## How it works

- The script fetches the original image URL from the Flickr API and submits it to astrometry.net
- Astrometry.net identifies star patterns and determines the sky coordinates (plate solving)
- The script retrieves calibration data (RA, Dec, orientation, field size, pixel scale) and annotations (identified objects)
- Each annotation is looked up in SIMBAD for common names
- Results are posted back to the Flickr photo as machine tags, a comment, and hover-over notes
- Annotations are prioritized: deep-sky objects first, then named stars, Bayer designations, Flamsteed numbers, and catalogue entries
- Flickr limits photos to 100 notes — the script automatically filters to the most interesting objects if needed

## Features

- Supports Flickr URLs, short links (`flic.kr`), guest pass URLs, and bare photo IDs
- SIMBAD name resolution for common object names
- Smart annotation filtering with priority tiers when notes exceed Flickr's 100-note limit
- Automatic retry on astrometry.net transient errors
- 30-minute solve timeout with retry command printed on timeout
- Tags failed solves with `astrometry:status=failed` so you know not to retry
- Handles both landscape and portrait images

## Dependencies

- [flickr-oauth](https://github.com/teleportaloo/flickr-oauth) — Flickr OAuth 1.0a authentication (installed automatically)
- Python 3.8+ standard library (no other external dependencies)

## License

MIT
