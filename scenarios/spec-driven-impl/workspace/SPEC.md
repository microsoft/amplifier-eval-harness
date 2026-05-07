# Roman Numerals — Specification

Implement two top-level functions in `/workspace/roman.py`:

## `roman_to_int(s: str) -> int`

Convert a Roman numeral string (e.g. `"MCMXCIV"`) to its integer value (`1994`).

### Letter values

| Letter | Value |
|---|---|
| I | 1 |
| V | 5 |
| X | 10 |
| L | 50 |
| C | 100 |
| D | 500 |
| M | 1000 |

### Subtractive notation (the only allowed pairs)

| Pair | Value |
|---|---|
| IV | 4 |
| IX | 9 |
| XL | 40 |
| XC | 90 |
| CD | 400 |
| CM | 900 |

No other subtractive pairs are valid (e.g. `IL`, `IC`, `VX`, `LC` are all invalid).

### Repetition rules

- `I`, `X`, `C`, `M` may repeat at most 3 consecutive times (`III` ok, `IIII` invalid).
- `V`, `L`, `D` may NOT repeat (each appears at most once in a numeral).

### Errors

`roman_to_int` MUST raise `ValueError` on:

- empty string
- lowercase letters (e.g. `"iv"`)
- characters outside `IVXLCDM`
- malformed sequences such as `"IIII"`, `"VV"`, `"IL"`, `"IC"`

## `int_to_roman(n: int) -> str`

Convert an integer in the range `1 ≤ n ≤ 3999` to uppercase Roman numerals using subtractive notation where appropriate.

### Errors

`int_to_roman` MUST raise `ValueError` on `n < 1` or `n > 3999`.

### Examples

| n | Output |
|---|---|
| 1 | I |
| 4 | IV |
| 9 | IX |
| 40 | XL |
| 1994 | MCMXCIV |
| 3999 | MMMCMXCIX |

## Round-trip property

For every valid `n` in `[1, 3999]`:

```
roman_to_int(int_to_roman(n)) == n
```
