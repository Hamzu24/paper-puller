paper-puller
============
[![Python](https://img.shields.io/badge/Python-3%2B-blue.svg)](https://www.python.org)

A tool to batch download research papers by title. Searches multiple open access sources and falls back to Sci-Hub when needed.

Features
--------
* **Batch download by title** - Give it a list of paper titles and it finds & downloads them
* **Multiple sources** - Tries arXiv, Unpaywall, Europe PMC, Semantic Scholar, OpenAlex, CrossRef, CORE, Internet Archive, and Sci-Hub
* **Smart title matching** - Uses fuzzy matching to find the right paper even with slight title variations
* **TUI viewer** - Browse and open downloaded papers with `paper_viewer.py`

Setup
-----
```
pip install -r requirements.txt
```

Usage
-----

### Batch download (main use case)

Create a `papers.json` file with paper titles organized by category:

```json
{
  "transformers": [
    "Attention Is All You Need",
    "BERT: Pre-training of Deep Bidirectional Transformers"
  ],
  "diffusion": [
    "Denoising Diffusion Probabilistic Models"
  ]
}
```

Then download a category:

```bash
# List available categories
python puller/pull.py --list

# Download all papers in a category
python puller/pull.py transformers

# Specify output directory
python puller/pull.py transformers -o ./my-papers/
```

### Download a single paper

```bash
# By DOI
python puller/pull.py -d "10.1038/nature12373"

# By arXiv ID
python puller/pull.py -d "arXiv:1706.03762"

# By URL
python puller/pull.py -d "https://arxiv.org/abs/1706.03762"

# By PMID
python puller/pull.py -d "12345678"
```

### Browse downloaded papers

```bash
python paper_viewer.py
```

Use arrow keys or j/k to navigate, Enter to open, / to search, q to quit.

### Other options

```
python puller/pull.py -h

optional arguments:
  -d, --download (DOI|PMID|URL)  Download a single paper
  -f, --file path                Download from file of identifiers
  -s, --search query             Search Google Scholar
  -sd, --search_download query   Search and download
  -l, --limit N                  Limit search results (default: 10)
  -o, --output path              Output directory (default: papers/)
  -v, --verbose                  Verbose output
  -p, --proxy                    Proxy (e.g., socks5://user:pass@host:port)
```

Library usage
-------------

```python
from puller.pull import SciHub

sh = SciHub()

# Download by DOI/URL/PMID
result = sh.download('10.1038/nature12373', destination='papers/')

# Fetch without saving
result = sh.fetch('arXiv:1706.03762')
# Returns: {'pdf': bytes, 'url': str, 'name': str}

# Batch download from JSON
sh.download_from_json('papers.json', 'transformers', 'papers/')
```

Known Limitations
-----------------
* Captchas may block requests after heavy usage
* Some papers may not be available through any source

License
-------
MIT
