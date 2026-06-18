# ToolShed

A library of shell and Python scripts for automating personal and creative workflows, designed to run as [CueQueue](https://github.com/blairg23/cuequeue) jobs.

## Tools

| Tool | Language | Description |
|------|----------|-------------|
| [FileMapper](FileMapper/) | Python | Match, rename, and move/copy files using configurable strategies (chronological, date-prefix, interactive fallback) |
| [FileMover](FileMover/) | Python | Bulk move or copy files/directories from a source to destination |
| [GDriveTools](GDriveTools/) | Shell | Rclone backup of a local directory to Google Drive |
| [GmailTools](GmailTools/) | Python | Gmail automation via OAuth -- label rules, filters (skeleton) |
| [SLOBSTools](SLOBSTools/) | Shell | Streamlabs OBS VOD archival using FileMapper |
| [TraktorProTools](TraktorProTools/) | Python + Shell | Traktor Pro backup suite: recordings, bundle, cleanup, cloud sync, m4a-to-FLAC conversion, collection repair |

## Usage

Python tools use [Poetry](https://python-poetry.org/) for dependency management:

```bash
cd FileMapper
poetry install
poetry run python src/filemapper.py --dry-run
```

## Adding a new tool

1. Create a directory: `ToolName/`
2. Add `config.yml` with configurable paths and settings
3. Add source in `ToolName/src/`
4. Use `dry`/`live` as the first argument for dry-run support

## CueQueue

Jobs in this repo are designed to be queued via [CueQueue](https://github.com/blairg23/cuequeue). Each script is self-contained: it reads config from `config.yml` and accepts a dry/live argument.
