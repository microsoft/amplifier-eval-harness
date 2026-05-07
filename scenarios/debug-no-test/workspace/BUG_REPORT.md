# Bug report

> **Reporter:** [user]
> **Severity:** medium

## Symptom

When I create a cache with `LRUCache(capacity=3)`, add three items
`("a",1)`, `("b",2)`, `("c",3)`, and then call `get("a")`, the cache
returns `None`. But I added "a" before "b" and "c" — it should still be
in there.

If I check `len(cache)` right after the third put, I get `2`, not `3`.

## Expected

Per the docstring, the cache should hold up to `capacity` items
(3 in this case). With three puts at capacity 3, all three should
be present and accessible. None of them should be evicted unless I add
a fourth.

## What I think is happening

I haven't traced it carefully but I suspect something around the
overflow check on `put` is off — like it's comparing the wrong thing
or running too eagerly.
