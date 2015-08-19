hold_stuff
==========

A couple of scripts to help us more quickly move things out of our temporary holding area and into our patron's hands. Results are available from a local server.

### make-treemap.py 
Generates a treemap based on item counts. Run by a cronjob each week. 

### hold.py 
Generates reports to help our catalogers quickly identify items in the hold with member copy -- the low-hanging fruit for quick cataloging and, therefore, even greater customer satisfaction. The main idea is that designated staff can download the relevant report at will, print it out, and head to the hold with the picklist, pencil, and a cart (we're not at the mobile device stage yet).

We're searching the following holds, based on language groups:
* Arabic (ara)
* Cyrillic (aze, bul, rus, ukr)
* Latin American (cat, por, spa)
* Roman (dut, eng, fre, ger, lat, ita, rum)
* Turkish (tur)

What hold.py does for each hold...

1. Query Voyager for items that are most likely to be in the given hold (we have no single identifier to let us know this). The query logic is below. 
2. Ping the [WorldCat Search API](http://www.oclc.org/developer/develop/web-services/worldcat-search-api.en.html) using ISBNs (a cache is checked first to be sure we're not abusing the service).
3. Output a csv file with data from Voyager and WorldCat to serve as a downloadable picklist.
4. Output an HTML page reports.html with a link to the csv file.


#### query logic

##### Voyager data
* Language: (varies by hold)
* ISBN: yes
* MFHD call no.: no
* Item Status: In Process
* 902: no
* <strike> Locations (non-SA): anxb, dixn, f, mus, sd, se, sh, st, sv, zeis, srel, ssrc, sxf, vidl, piapr, anxa, dc, dr, ppl, sc, scp, shs, slav, spc, spia, spr, anxbl, rcppa, clas, docs, fis, rcppk, rcpqk, scl, sci, egsr
* Locations (SA): sa, saph, rcppj</strike>
* Either a subject [600, 610, 611, 65x] *or* literary form is something other than "0" or " "

##### WorldCat data
What we're checking in the MARCXML returned from WorldCat...
* leader/18 - encoding level
* 008/35-37 - language
* 008/33 - literary form
* 001 - OCLC number
* 042$a - authentication code
* 050_ _ - LC call number
* 090 - shelf location
* 600, 610, 611, 65[^3] - subjects

The main scripts are above, but the full working structure is like this (a few directories need to be created):
```
hold
├── config
│   └── hold.cfg
├── db
│   └── cache.db
├── hold.py
├── html
│   └── hold
│       ├── coffee*
│       ├── css*
│       ├── data
│       ├── images
│       ├── index.html
│       ├── js*
│       ├── multiples.html
│       ├── reports.html
│       ├── stacked.html
│       ├── summaries
│       └── treemap.html
├── logs
└── make-treemap.py

*These dirs are from Jim Vallandingham's source code: http://flowingdata.com/2014/10/15/linked-small-multiples/
```

### requirements
* [cx_Oracle](http://cx-oracle.sourceforge.net/) 
* [requests](http://docs.python-requests.org/en/latest/user/install/)
* [pyMarc](https://github.com/edsu/pymarc)
