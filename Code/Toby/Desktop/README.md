# Raspberry Pi Config Studio

This repo now includes a PySide6 desktop app for creating a fresh Raspberry Pi `config.txt` from scratch or loading and editing an existing one.

## Run

```bash
python3 main.py
```

## What it includes

- Blank document on startup
- Native-style menu bar with File, Edit, Insert, Tools, View, and Help menus
- Quick insertion for common Raspberry Pi `config.txt` directives and conditional filters
- Save, Save As, Revert, and Open workflows
- Built-in validation for line length, malformed entries, and duplicate single-value keys
- Local reference dock that shows `configtxt-docs.txt`
