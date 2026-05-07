# greeter

A tiny Python CLI that greets people in different languages, with optional
sentiment shaping based on time-of-day.

## Usage

```bash
python -m greeter --name Alice --language en
python -m greeter --name Bob --language ja --mood formal
```

## Layout

```
greeter/
├── __main__.py        # CLI entry point
├── greetings.py       # Translation table
├── moods.py           # Time-of-day → mood mapping
└── render.py          # Final string assembly
```
