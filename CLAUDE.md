# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

scihub.py is an unofficial Python API for Sci-Hub that enables searching for papers on Google Scholar and downloading papers from Sci-Hub. It works both as a library and as a command-line tool.

## Setup

```bash
pip install -r requirements.txt
```

Dependencies: beautifulsoup4, requests, retrying, pysocks

## Commands

### Command-line usage

```bash
# Download a paper by DOI, PMID, or URL
python scihub/scihub.py -d "10.1000/xyz123"

# Search Google Scholar
python scihub/scihub.py -s "query" -l 10

# Search and download
python scihub/scihub.py -sd "query" -o ./papers/

# Download from file of identifiers
python scihub/scihub.py -f identifiers.txt -o ./papers/

# Use proxy
python scihub/scihub.py -d "10.1000/xyz123" -p "socks5://user:pass@host:port"
```

### Library usage

```python
from scihub import SciHub
sh = SciHub()

# Fetch paper (returns dict with 'pdf', 'url', 'name')
result = sh.fetch('DOI or URL')

# Download paper to disk
result = sh.download('DOI or URL', path='paper.pdf')

# Search Google Scholar
results = sh.search('query', limit=10)
```

## Architecture

Single-file library at `scihub/scihub.py` containing:

- **SciHub class**: Main API class that manages HTTP sessions, discovers available Sci-Hub mirrors, and handles paper fetching/downloading
- **CaptchaNeedException**: Custom exception raised when Sci-Hub blocks requests with captcha
- **main()**: CLI entry point using argparse

Key implementation details:
- Sci-Hub mirrors are auto-discovered via `https://sci-hub.now.sh/`
- Papers are fetched by finding the PDF iframe source on Sci-Hub pages
- Identifier types (DOI, PMID, URL) are auto-classified in `_classify()`
- Downloads retry up to 10 times with randomized backoff (via `@retry` decorator)
- SSL verification is disabled for Sci-Hub requests due to certificate chain issues

## Known Limitations

- Captchas can block searches/downloads after heavy usage
- The mirror discovery URL (`sci-hub.now.sh`) may become outdated
