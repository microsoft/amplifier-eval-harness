# CSV Parser — Specification

Implement `parse_csv(text: str) -> list[list[str]]` in `/workspace/csv_parser.py`.

This spec is RFC 4180 with three named extensions (whitespace preservation, CRLF line endings, BOM handling). Read every section.

## 1. Basic shape

A CSV document is a sequence of records (rows) separated by line endings. Each record is a sequence of fields (cells) separated by commas (`,`).

```
a,b,c
1,2,3
```

parses to `[["a", "b", "c"], ["1", "2", "3"]]`.

## 2. Empty input

`parse_csv("")` returns `[]` (the empty list).

## 3. Empty fields

A run of two commas means an empty field between them.

```
a,,b      ->  [["a", "", "b"]]
,a,       ->  [["", "a", ""]]
,,        ->  [["", "", ""]]
```

## 4. Trailing commas / leading commas

Leading and trailing commas produce empty fields:

```
,a    ->  [["", "a"]]
a,    ->  [["a", ""]]
```

A trailing comma at the end of input is exactly one trailing empty field on the last row.

## 5. Line endings

Both `\n` (LF) and `\r\n` (CRLF) terminate records. A bare `\r` does NOT terminate a record (treat as a literal character — though it shouldn't appear in well-formed CSV).

A trailing newline at the end of input does NOT produce an extra empty record.

```
"a\nb\n"     ->  [["a"], ["b"]]
"a\nb"       ->  [["a"], ["b"]]
"a\r\nb\r\n" ->  [["a"], ["b"]]
```

A blank line in the middle of the document IS a record with one empty field:

```
"a\n\nb\n"   ->  [["a"], [""], ["b"]]
```

## 6. Quoted fields

A field that BEGINS with `"` (double-quote) is a quoted field. The closing quote is required. Inside a quoted field:

- Commas are literal (do not split fields).
- Newlines are literal (do not terminate records).
- Two consecutive quotes (`""`) represent one literal `"`.
- Whitespace is preserved exactly as written.

```
'"a,b","c"'        ->  [["a,b", "c"]]
'"line\n2"'        ->  [["line\n2"]]   (where \n is a real newline)
'"he said ""hi"""' ->  [['he said "hi"']]
```

A quoted field MUST be closed before the next comma or line ending. If the closing quote is missing entirely (i.e. the input ends mid-quote), raise `ValueError`.

## 7. Whitespace handling

- **Inside a quoted field:** whitespace is preserved exactly.
- **In an unquoted field:** whitespace is preserved exactly (do NOT trim).

```
" a , b "          ->  [[" a ", " b "]]
'" a "," b "'      ->  [[" a ", " b "]]
```

Whitespace BETWEEN a closing quote and the next comma/newline is silently consumed (forgiving):

```
'"a"  ,"b"  '      ->  [["a", "b"]]
```

## 8. BOM (byte-order mark)

If the input starts with a UTF-8 BOM (`\ufeff`), strip it before parsing. BOMs anywhere else in the input are treated as literal characters.

```
'\ufeffa,b'        ->  [["a", "b"]]
```

## 9. Errors

Raise `ValueError` on:

- An unclosed quoted field (input ends with `"` still open).
- A character other than comma, line ending, or whitespace immediately after a closing quote.
  - Example: `'"a"x,b'` is invalid (`x` follows the closing `"`).

## 10. Examples — putting it all together

```python
parse_csv('a,b\nc,d\n')                              == [["a", "b"], ["c", "d"]]
parse_csv('"a,b",c\n')                               == [["a,b", "c"]]
parse_csv('"a""b",c')                                == [['a"b', "c"]]
parse_csv(',\n,\n')                                  == [["", ""], ["", ""]]
parse_csv('a\r\nb\r\n')                              == [["a"], ["b"]]
parse_csv('"line1\nline2",end')                      == [["line1\nline2", "end"]]
parse_csv('\ufefffirst,second')                      == [["first", "second"]]
parse_csv('  a  ,  b  ')                             == [["  a  ", "  b  "]]
```
