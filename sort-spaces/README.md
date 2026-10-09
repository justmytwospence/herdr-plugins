# Herdr Sort Spaces

A Herdr plugin that sorts spaces (workspaces) alphabetically by label. Herdr has
no built-in command for it; this uses the socket API's `workspace.move`.

Sorting is case-insensitive and stable: spaces with the same label keep their
relative order. Only out-of-place spaces are moved, so an already-sorted sidebar
makes no changes.

Requires Herdr 0.9.3+ and Python 3.9+ (stdlib only).

## Actions

| Action | Title |
|---|---|
| `sort` | Sort spaces alphabetically |
| `sort-reverse` | Sort spaces alphabetically (Z-A) |

Run them from Herdr's action menu, or:

```sh
herdr plugin action invoke sort --plugin sort-spaces
```

The script also runs standalone against the current server, with `--dry-run` to
print the moves without making them:

```sh
python3 sort_spaces.py --dry-run
```

## Installation

```sh
herdr plugin install justmytwospence/herdr-sort-spaces
```

For development, link the checkout:

```sh
herdr plugin link ~/Projects/herdr-sort-spaces
```

## Tests

```sh
python3 -B -m unittest discover -s tests
```
