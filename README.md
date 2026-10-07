# rsp-fares-feed

Parse the RSP Fares and Associated Data Feed, every permanent rail fare
published in Great Britain, into a table you can query.

The Rail Delivery Group and Rail Settlement Plan publish the feed. It
arrives as a 300 MB zip of fixed-width text files with no delimiters
and no headers, where each field is identified by its character
position in a PDF specification. Prices are in pence, stations are NLC
numbers, ticket types are three-letter codes, and about 7.6 million
fares are spread across two record types that have to be joined by ID.

```python
from pathlib import Path
from rsp_fares_feed import clean_feed, build_fares_table, lookup_pair

clean_feed(Path("fares-raw"), Path("fares"))
table = build_fares_table(Path("fares"))
lookup_pair(table, "LIV", "MAN")
```

## Install

Python 3.11 or later.

```sh
pip install git+https://github.com/rosedillon/rsp-fares-feed
```

or, from a clone, `pip install .`. It depends on pandas. HTTP goes
through `urllib` from the standard library.

## Getting the feed

You need a free [National Rail Data Portal](https://opendata.nationalrail.co.uk)
account.

```sh
export NRDP_USERNAME="you@example.com"
export NRDP_PASSWORD="..."

rsp-fares-feed download -o fares-raw
rsp-fares-feed clean fares-raw -o fares
```

Credentials are read only from the environment. There is no
`--password` flag and no config setting for one, so they cannot end up
in shell history or a committed config file.

[RSPS5045](https://www.raildeliverygroup.com/files/Publications/services/rsp/RSPS504502-00FaresandAssociatedDataFeedInterfaceSpecification.pdf)
specifies the format, and the
[Open Rail Data wiki](https://wiki.openraildata.com/index.php?title=DTD)
has background on the feed as a whole.

Cleaning the national feed gives:

```console
$ rsp-fares-feed clean fares-raw -o fares
flow         7,612,617
locations        3,449
tickets          3,448
```

## Looking up fares

```console
$ rsp-fares-feed lookup LIV MAN -d fares
LIV -> MAN
ticket_code ticket_description  fare_gbp restriction_code toc route_code
        DGA       SALE ADVANCE       0.5               NT NTH      00429
        DGB       SALE ADVANCE       1.0               NT NTH      00429
        DGC       SALE ADVANCE       2.0               NT NTH      00429
        NAP    FOR TEST USE ON       2.3               NT NTH      00429
        DG0            ADVANCE       2.7               NT NTH      00429
        DG1            ADVANCE       3.2               NT NTH      00429
...
        NLY            ADVANCE      20.1               AE RWB      00216
        NMY            ADVANCE      21.5               AE RWB      00216

Median standard-class Advance: £10.10
```

The real output has 27 fares; the middle is cut here. Add `--json` for
machine-readable output, or `--csv out.csv` to write a file.

## Two things the feed does not make obvious

### Advance fares are a ladder

`DG0` to `DG9`, `V2A` to `V2Z`, `USA` to `USZ` and `NAY` to `NMY` are
not different products. They are the rungs of an operator's
yield-managed Advance ladder, all described as `ADVANCE`. There is no
single Advance price; what a passenger pays depends on how far ahead
they book and what is left.

`median_advance_fare()` takes the median across the published ladder.
It only counts standard class retail fares described exactly as
`ADVANCE`, so first class (`ADVANCE 1ST`) and standard premium
(`ADVANCE STDPREM`) are left out:

```python
median_advance_fare(table, "LIV", "MAN")  # 10.10
median_advance_fare(table, "LDS", "YRK")  # 7.70
```

It is a stand-in for the expected price in revenue work, not a quoted
price.

### Many "fares" are not prices

The feed holds far more than a passenger can buy. Negotiated products
(Group, Tour Operator, School Group, Special Events, operator
complimentary travel) have no advertised price, so they are published
at a round placeholder (£999.00, £999.99 or £9,998.00) on every route in
the country. Wholesale and settlement products (Inclusive Tour, staff,
child concession) carry a token price of a few pence with no link to
distance.

Both kinds are flagged in a `non_retail_fare` column. Nothing is
dropped, so you can decide what to keep:

```python
from rsp_fares_feed import retail_only

retail_only(lookup_pair(table, "LIV", "MAN"))
```

The CLI hides them by default and says how many it hid. Between London
Euston and Manchester more than a third of the published fares are not
prices anyone can buy:

```console
$ rsp-fares-feed lookup EUS MAN -d fares
note: 65 of 178 fare(s) are non-retail placeholders (group, tour operator, settlement
tokens) and are hidden; pass --include-non-retail to show them.
```

Both patterns were checked against the full national feed. The
thresholds are named constants in
[`table.py`](src/rsp_fares_feed/table.py) if you want to change them.

## Limitations

- There are no dated prices and no Advance availability. The feed holds
  the permanent fares. What is on sale for a given day is in each
  operator's own retail system, so nothing here can price an exact
  journey on an exact date.
- Cluster fares are not resolved. A fare can be set against a cluster
  NLC covering several stations, such as a group of London terminals,
  instead of one station. Fares are joined on a station's own NLC only,
  so a station whose fares are published only for its cluster returns
  nothing. Resolving those needs the separate Station Clusters file,
  which is not read.
- Routeing is not interpreted. `route_code` is reported as published;
  whether a ticket is valid on a particular train is a question for the
  National Routeing Guide.
- Restriction codes are reported but not explained. The `.RST` file
  that describes them is not parsed.
- Some ticket types are for testing (`NAP`, "FOR TEST USE ON", above)
  and are not flagged. Check the description if a price looks odd.
- A flow marked `R` is reversible, and `lookup_pair` relabels it so it
  reads in the direction you asked for. A flow marked `S` has no fare
  the other way, so the reverse lookup is empty.
- The feed is republished roughly three times a year, so download it
  again rather than keep an old copy.

## Python API

| Name | |
|---|---|
| `fetch_feed(dest)` | log in to NRDP, download the feed and extract it |
| `clean_feed(raw, out)` | parse `.FFL`, `.LOC` and `.TTY` into three CSVs |
| `build_fares_table(dir)` | join them into one row per fare |
| `lookup_pair(table, o, d)` | fares for a pair, cheapest first, reversible flows included |
| `median_advance_fare(table, o, d)` | median standard class Advance fare, in pounds |
| `flag_non_retail(flow)` | a boolean Series marking placeholder and settlement prices |
| `retail_only(fares)` | drop the flagged rows |

Every error is a subclass of `FaresFeedError`.

## Development

```sh
git clone https://github.com/rosedillon/rsp-fares-feed
cd rsp-fares-feed
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check . && mypy src
```

The tests build synthetic feed files at the same character positions
as the real ones, and use a scripted fake in place of `urlopen` for the
download, so login, token handling and extraction are tested with no
network or credentials. No licensed data is committed.

## Licence

GPL-3.0. See [LICENSE](LICENSE).

Not affiliated with or endorsed by the Rail Delivery Group, Rail
Settlement Plan or National Rail.
