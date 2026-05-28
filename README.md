# flickr-plate-solve

Plate-solve Flickr astrophotography images using [astrometry.net](https://nova.astrometry.net) and annotate them with machine tags, comments, and hover-over notes identifying stars and deep-sky objects.

Supports **local solving** (instant, via `solve-field`) and **remote solving** (queued, via the nova.astrometry.net cloud service). If `solve-field` is installed locally, it is used by default.

## What it does

1. Takes a Flickr photo URL (or short link, guest pass URL, or bare photo ID)
2. Plate-solves the image (locally or via astrometry.net)
3. Posts results back to Flickr as:
   - **Machine tags** — `astrometry:ra`, `astrometry:dec`, `astrometry:orientation`, etc.
   - **Comment** — human-readable calibration summary with identified objects
   - **Notes** — hover-over annotations on the image identifying stars and deep-sky objects (with SIMBAD lookups for magnitudes, distances, and object types)

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

## Local solving (recommended)

For instant results without queuing, install astrometry.net's `solve-field` locally:

```bash
# macOS (Homebrew)
brew install astrometry-net

# Debian/Ubuntu
sudo apt install astrometry.net astrometry-data-2mass-08-19
```

You also need index files covering your typical field of view. The 4200-series (4208–4219) covers roughly 2 arcmin to 2 degrees, which suits most amateur astrophotography. On macOS with Homebrew, download them into the data directory:

```bash
DATA_DIR="$(brew --prefix astrometry-net)/data"
for i in $(seq 4208 4219); do
    curl -o "$DATA_DIR/index-${i}.fits" "https://data.astrometry.net/4200/index-${i}.fits"
done
```

When `solve-field` is found in your PATH (or common Homebrew locations), it is used automatically. Use `--remote` to force the cloud service instead.

## API keys

You need Flickr API keys for all modes. An astrometry.net API key is only needed for remote solving.

| Key | Where to get it | Required for |
|-----|----------------|--------------|
| Flickr API Key | [flickr.com/services/apps/create/apply](https://www.flickr.com/services/apps/create/apply/) | All modes |
| Flickr API Secret | Same page as above | All modes |
| Astrometry.net API Key | [nova.astrometry.net](https://nova.astrometry.net) — My Profile → API Key (free) | Remote solving only |

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

# Force local or remote solving
python3 plate_solve.py --local https://flic.kr/p/2seqonc
python3 plate_solve.py --remote https://flic.kr/p/2seqonc

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

## Album scripts

### solve_album.py — plate-solve an entire album

```bash
# Solve all photos (skips already-solved and previously-failed)
python3 solve_album.py https://www.flickr.com/photos/user/albums/72177720326735849/

# Dry run — see what would be solved
python3 solve_album.py --dry-run 72177720326735849

# Force re-solve everything, even already-solved photos
python3 solve_album.py --force 72177720326735849
```

With local solving each photo takes seconds; with remote solving each can take up to 30 minutes in the queue. Photos tagged `astrometry:status=solved` or `astrometry:status=failed` are skipped unless `--force` is used.

### retag_album.py — re-tag already-solved photos

Re-does the notes on photos that have already been plate-solved (e.g. after updating the annotation format). Finds the existing astrometry.net job ID from the photo's comments, so no re-solving is needed.

```bash
# Re-tag all solved photos in an album
python3 retag_album.py https://www.flickr.com/photos/user/albums/72177720326735849/

# Dry run
python3 retag_album.py --dry-run 72177720326735849
```

## How it works

### Local solving (default when solve-field is installed)

- Fetches the original image URL from the Flickr API
- Downloads the image and runs `solve-field` locally (with 10 minutes CPU time limit)
- `plot-constellations` identifies NGC/IC/Messier objects and named stars in the solved field
- Each object is looked up in [SIMBAD](https://simbad.cds.unistra.fr/) for common names, magnitudes, distances, and object types
- Results are posted back to Flickr as machine tags, a comment, and hover-over notes

### Remote solving (fallback, or with --remote)

- Submits the image URL to nova.astrometry.net for cloud-based plate solving
- Waits for the solve to complete (can take up to 30 minutes in the queue)
- Retrieves calibration data and annotations from the astrometry.net API
- Each annotation is looked up in SIMBAD for common names
- Results are posted back to Flickr

### Common to both

- Annotations are prioritised: deep-sky objects first, then named stars, Bayer designations, Flamsteed numbers, and catalogue entries
- Flickr limits photos to 100 notes — the script automatically filters to the most interesting objects if needed

## Features

- **Local and remote solving** — instant local solves via `solve-field`, with automatic fallback to nova.astrometry.net
- Supports Flickr URLs, short links (`flic.kr`), guest pass URLs, and bare photo IDs
- SIMBAD name resolution for common names, object type, visual magnitude, and distance
- Notes show e.g. "NGC 6205, Hercules Globular Cluster, M 13 (Globular Cluster, mag 5.8, 26.1 kly)"
- Smart annotation filtering with priority tiers when notes exceed Flickr's 100-note limit
- Album-level scripts for batch solving and retagging
- Tags failed solves with `astrometry:status=failed` so you know not to retry
- Handles both landscape and portrait images

## Dependencies

- [flickr-oauth](https://github.com/teleportaloo/flickr-oauth) — Flickr OAuth 1.0a authentication (installed automatically)
- [astrometry.net](https://github.com/dstndstn/astrometry.net) — local plate solving (`solve-field` and `plot-constellations`). Optional but recommended.
- Python 3.8+ standard library (no other external dependencies)

## License

MIT
