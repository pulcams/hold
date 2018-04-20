#!/usr/bin/env python
#-*- coding: utf-8 -*-

"""
hold.py
Generate reports to facilitate getting stuff out of the holds.
Run like `python hold.py -qp`
Do `python hold.py -h` for (sparse) details.
NOTE: use absolute paths when running with crontab, relative paths won't work
from 20150826
pmg
"""

import argparse
import codecs
import ConfigParser
import csv
import cx_Oracle
import logging
import random
import re
import requests
import sqlite3 as lite
import sys
import time
from datetime import date, datetime, timedelta
from lxml import etree

#TODO
# check 050 and 090 for presence of 0-9

#TODO?
# add BOM or UnicodeWriter for opening in Excel
# no items for tur?
# Generate PDFs?
# jinja for make_html?
# add the 'confirm' items from member copy policy (245, 250, etc.)?

# config
config = ConfigParser.RawConfigParser()
config.read('./config/hold_local.cfg') # <= local for debugging
config.read('/var/www/hold/config/hold.cfg') # <= production

USER = config.get('vger', 'user')
PASS = config.get('vger', 'pw')
PORT = config.get('vger', 'port')
SID = config.get('vger', 'sid')
HOST = config.get('vger', 'ip')

DB_FILE =  config.get('local', 'db')
TEMPFILE = config.get('local', 'temp')
TEMPFILE += 'out.csv'
CSVOUT = config.get('local', 'csv') # data from each hold's report
HTMLOUT = config.get('local', 'html') # reports.html
LOG = config.get('local', 'log')
SUMMARIES = config.get('local', 'summaries') # summary counts (and data for sparklines)

today = time.strftime('%Y%m%d') # name log files
todaydb = time.strftime('%Y-%m-%d') # date to check against db
justnow = time.strftime("%m/%d/%Y") # freshness date of reports


def main(hold, query=None, ping=None,  firstitem=0, lastitem=0):
	"""
	the engine with, a little function chain
	"""

	logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p',filename=LOG+today+'.log',level=logging.INFO)
	
	# the following two lines disable the default logging of requests.get()
	logging.getLogger("requests").setLevel(logging.WARNING)
	logging.getLogger("urllib3").setLevel(logging.WARNING)
		
	logging.info('START ' + '-' * 20)
	logging.info('hold: '+ hold)
	
	if query:
		query_vger(hold, firstitem, lastitem)

	if ping:
		ping_worldcat(hold)

	write_summaries(hold)

	make_html()
		
	logging.info('-' * 23 + 'END')


def query_vger(hold, firstitem=0, lastitem=0):
	"""
	query vger => temp csv file
	"""
	logging.info("querying Voyager")
	langs = ""
	lang_cond = "" # for negating a set of languages
	place = "" # only for latin_american
	parens = "" # ditto
	vendor = "" # ditto (the field name)
	vendors = "" # ditto (the joins)
	isbn = "AND REGEXP_REPLACE(BIB_TEXT.ISBN,'\s.*','') is not null" # only include null isbn for latin_american
	locs = "'6','7','13','20','21','22','24','46','84','96','138','140','142','144','163','165','171','195','197','204','214','217','221','229','250','281','287','372','419','444','446','448','450','468','492','523'"
	sa_locs = "'123','129','423'"
	ues_loc = "'273'"
	
	if hold == 'roman':
		langs = "'eng','fre','ger','ita','dut','rum','lat'"
	elif hold == 'latin_american':
		langs = "'spa','por','cat'"
		#place = ", TRIM(REGEXP_REPLACE(BIB_TEXT.PUB_PLACE,':\s+\z','')) as PUB_PLACE"
		place = ", SUBSTR(princetondb.GETALLBIBTAG(BIB_TEXT.BIB_ID,'008'),16,3) as PUB_PLACE"
		vendor = ", VENDOR.VENDOR_CODE"
		isbn = ""
		parens = "((("
		vendors = "INNER JOIN LINE_ITEM ON BIB_TEXT.BIB_ID = LINE_ITEM.BIB_ID) INNER JOIN PURCHASE_ORDER ON LINE_ITEM.PO_ID = PURCHASE_ORDER.PO_ID) INNER JOIN VENDOR ON PURCHASE_ORDER.VENDOR_ID = VENDOR.VENDOR_ID)"
	elif hold == 'turkish':
		langs = "'tur'"
	elif hold == 'arabic':
		langs = "'ara'"
	elif hold == 'persian':
		langs = "'per'"
	elif hold == 'cyrillic':
		langs = "'rus', 'aze', 'bul', 'ukr', 'srp', 'bel', 'bos', 'cnr', 'geo'"
		locs = locs + ',' + sa_locs + ',' + ues_loc
	elif hold == 'greek':
		langs = "'gre','grc'"
	elif hold == 'hebrew':
		langs = "'heb'"
	elif hold == 'cjk_art':
		langs = "'chi','jpn','kor'"
		locs = sa_locs + ',' + ues_loc
	elif hold == 'art':
		langs = "'chi','jpn','kor'"
		lang_cond = 'NOT'
		locs = sa_locs
		
	if firstitem > 0 or lastitem > 0:
		items = "AND ITEM_STATUS.ITEM_ID between '%s' and '%s'" % (firstitem,lastitem)
	else:
		items = ""
	
	query = """SELECT BIB_TEXT.LANGUAGE, ITEM_STATUS.ITEM_ID, BIB_TEXT.BIB_ID, REGEXP_REPLACE(BIB_TEXT.ISBN,'\s.*','') AS ISBN, SUBSTR(BIB_TEXT.TITLE_BRIEF,1,25), LOCATION.LOCATION_NAME,
		ITEM.CREATE_DATE %s%s 
		FROM 
		((((((%sBIB_TEXT
		INNER JOIN BIB_MASTER ON BIB_TEXT.BIB_ID = BIB_MASTER.BIB_ID 
		INNER JOIN BIB_MFHD ON BIB_TEXT.BIB_ID = BIB_MFHD.BIB_ID) %s
		INNER JOIN MFHD_MASTER ON BIB_MFHD.MFHD_ID = MFHD_MASTER.MFHD_ID) 
		INNER JOIN MFHD_ITEM ON MFHD_MASTER.MFHD_ID = MFHD_ITEM.MFHD_ID)
		INNER JOIN ITEM ON MFHD_ITEM.ITEM_ID = ITEM.ITEM_ID) 
		INNER JOIN ITEM_STATUS ON MFHD_ITEM.ITEM_ID = ITEM_STATUS.ITEM_ID) 
		INNER JOIN ITEM_STATUS_TYPE ON ITEM_STATUS.ITEM_STATUS = ITEM_STATUS_TYPE.ITEM_STATUS_TYPE) 
		INNER JOIN LOCATION ON MFHD_MASTER.LOCATION_ID = LOCATION.LOCATION_ID
		WHERE 
		BIB_TEXT.LANGUAGE %s IN (%s) %s
		AND ITEM_STATUS_TYPE.ITEM_STATUS_TYPE ='22'
		AND princetondb.GETBIBSUBFIELD(BIB_TEXT.BIB_ID, '902','a') is null
		AND MFHD_MASTER.NORMALIZED_CALL_NO is null %s
		AND BIB_MASTER.SUPPRESS_IN_OPAC = 'N'
		AND LOCATION.LOCATION_ID in (%s)
		ORDER BY BIB_TEXT.LANGUAGE, ITEM_STATUS.ITEM_ID""" % (place, vendor, parens, vendors, lang_cond, langs, isbn, items,locs)

	DSN = cx_Oracle.makedsn(HOST,PORT,SID)	
	oradb = cx_Oracle.connect(USER,PASS,DSN)
		
	rows = oradb.cursor()
	rows.execute(query)
	r = rows.fetchall()
	rows.close()
	oradb.close()

	with open(TEMPFILE,'wb+') as outfile:
		writer = csv.writer(outfile)
		header = ['LANGUAGE', 'ITEM_ID', 'BIB_ID', 'ISBN', 'TITLE_BRIEF','LOCATION_NAME','CREATE_DATE']
		if hold == "la": # not strictly necessary, but for clarity...
			header.append('PUB_PLACE')
			header.append('VENDOR')
		writer.writerow(header) 
		for row in r:
			writer.writerow(row)


def ping_worldcat(hold):
	"""
	check isbn against worldcat search api
	"""
	logging.info('pinging worldcat')
	outfile = CSVOUT + hold + '.csv'
	wskey = config.get('wc','wskey')
	ismember = False
	NS = {'marcxml':'http://www.loc.gov/MARC21/slim'}
	DIAGNS = {'diag':'http://www.loc.gov/zing/srw/diagnostic'}
	
	# sqlite connection
	con = lite.connect(DB_FILE)
	cached_date = None
	datediff = 0

	with open(TEMPFILE,'rb') as indata:
		reader = csv.reader(indata,delimiter=',', quotechar='"')
		firstline = reader.next()
		
		# output a new csv file (the downloadable report)
		header = ['lang','cat_lang','item_id', 'bib_id', 'isbn', 'oclc num', 'elvi','title','callno','loc','item created','lc_copy','pcc']
		
		if hold == 'latin_american':
			header.append('place')
			header.append('vendor')
			
		header.append(justnow) # just add the date as the last col heading
		
		with codecs.open(outfile,'wb+','utf-8') as out:
			out.write(u'\ufeff') # inserting the BOM so Unicode can be displayed in Excel
			writer = csv.writer(out)
			writer.writerow(header)
			
		for line in reader:
			lc = 'no'
			pcc = 'no'
			callno = ''
			lit = ''
			elvi = ''
			erec = ''
			lang = ''
			oclcnum = ''
			guess = ''
			isbn = ''
			field042 = ''
			field040b = ''
			field050_value = ''
			field090_value = ''
			field050 = False
			field090 = False
			field6xx = False
			msg = ''
			vlang = line[0]
			itemid = line[1]
			bibid = line[2]
			isbn = str(line[3]).strip()
			ti = unicode(line[4],'utf-8','replace') # unicode replacement char \ufffd results from truncating titles so...
			ti = ti.replace(u'\ufffd','').encode('utf-8') # ...strip it out.
			loc = line[5]
			created = line[6]
			
			try:
				place = line[7]
			except:
				place = ''
			
			try:
				vendor = line[8]
			except:
				vendor = ''
				
			with con:
				con.row_factory = lite.Row
				cur = con.cursor()
				cur.execute("SELECT date FROM items WHERE item_id=?",(itemid,))
				rows = cur.fetchall()
				if len(rows) == 0:
					cached_date = None
				else:
					for row in rows:
						cached_date = row['date']
			
			# get number of days since bib was last checked
			if cached_date is not None:
				date2 = datetime.strptime(todaydb,'%Y-%m-%d')
				date1 = datetime.strptime(str(cached_date),'%Y-%m-%d')
				datediff = abs((date2 - date1).days)
				
			# check cache -- don't ping worldcat unless we have to
			if cached_date is None or datediff >= maxage:
			#if isbn is not None and isbn != '' and isbn !=':': # <= for debugging
				if isbn is not None and isbn != '' and (not re.search('[a-zA-Z:]',isbn) or re.search('[xX]?$',isbn)): # sometimes text has been mistakenly entered into 020$a
					times = [1.5,2,2.25,2.5,3]
					randomsnooze = random.choice(times)
					connect_timeout = 7.0
					url = "http://www.worldcat.org/webservices/catalog/content/isbn/"+isbn+"?servicelevel=full&wskey="+wskey
					
					try:
						r = requests.get(url,timeout=(connect_timeout))
					except requests.exceptions.Timeout as e:
						msg = 'timeout'
					except requests.exceptions.HTTPError as e:
						msg = 'HTTPError'
					except requests.exceptions.ConnectionError as e:
						msg = 'Connection error'
					except requests.exceptions.TooManyRedirects as e:
						msg = 'Too many redirects'
					except requests.exceptions.InvalidSchema as e:
						msg = 'Invalid schema'
					except:
						msg = str(sys.exc_info()[0])
					if msg != '':
						logging.info('%s %s' % (msg,url))
					
					if verbose:
						print(url)

					if r.status_code == 200:
						tree = etree.fromstring(r.content)
						# NOTE: etree doesn't support XPath > 1.0 (!)
						if not tree.xpath("/diagnostics/text()"): # ...because when a record isn't found, the response code is still 200
							ldr = tree.xpath("//marcxml:leader/text()",namespaces=NS)
							field008 = tree.xpath("//marcxml:controlfield[@tag='008']/text()",namespaces=NS)
							field001 = tree.xpath("//marcxml:controlfield[@tag='001']/text()",namespaces=NS)
							field040b = tree.xpath("//marcxml:datafield[@tag='040']/marcxml:subfield[@code='b']/text()",namespaces=NS)
							field050_ind1 = tree.xpath("//marcxml:datafield[@tag='050']/@ind1='0'",namespaces=NS) # 050 0_ - LC call number ind1
							field050_ind2 = tree.xpath("//marcxml:datafield[@tag='050']/@ind2='0'",namespaces=NS) # 050 _0 - LC call number ind2
							field050 = tree.xpath("//marcxml:datafield/@tag='050'",namespaces=NS) # 050 - simply tests for existence of 050
							field050a_value = tree.xpath("//marcxml:datafield[@tag='050']/marcxml:subfield[@code='a']/text()",namespaces=NS)
							field050b_value = tree.xpath("//marcxml:datafield[@tag='050']/marcxml:subfield[@code='b']/text()",namespaces=NS)
							field042 = tree.xpath("//marcxml:datafield[@tag='042']/marcxml:subfield[@code='a']/text()",namespaces=NS) # 042$a - authentication code
							field090 = tree.xpath("//marcxml:datafield/@tag='090'",namespaces=NS) # 090 - shelf location
							field090a_value = tree.xpath("//marcxml:datafield[@tag='090']/marcxml:subfield[1]/text()",namespaces=NS)
							field090b_value = tree.xpath("//marcxml:datafield[@tag='090']/marcxml:subfield[2]/text()",namespaces=NS)
							field6xx_all = tree.xpath("//marcxml:datafield/@tag[starts-with(.,'6')]",namespaces=NS) #600, 610, 611, 65[^3] - subjects
							field6xx = any(x in ['600', '610', '611','650','651','654','655','656','677','658'] for x in field6xx_all)
							elvi = ldr[0][17]
							lit = field008[0][33]
							lang = field008[0][35:38]
							erec = field008[0][23] # s or o?
							oclcnum = field001[0]
							callno = tree.xpath("//marcxml:datafield[@tag='050']/marcxml:subfield[@code='a']/text()",namespaces=NS)
							callno += tree.xpath("//marcxml:datafield[@tag='090']/marcxml:subfield[@code='a']/text()",namespaces=NS)
							callno = '|'.join(callno)
							# from member copy cat policy checklist, possibly include later...
							#date = field008[0]
							#place = field008[0]
							#field300
							#field245
							#field250
							#field260/264

							field050_value = field050a_value + field050b_value
							field090_value = field090a_value + field090b_value

							#Test call nos for 0-9
							if len(field050_value) > 0:
								if not re.search('[0-9]',field050_value[0],re.IGNORECASE):
									field050 = False
							if len(field090_value) > 0:
								if not re.search('[0-9]',field090_value[0],re.IGNORECASE):
									field090 = False

							# lang of cataloging in 040$b
							if len(field040b) == 0:
								field040b = ''
							else:
								field040b = ', '.join(field040b)
									
							# member copy?
							if (field050 == True or field090 == True) and (field6xx == True or (lit != '0' and lit != ' ')) and (erec not in ['s','o']):
								ismember = True
								guess = 'member'
							else:
								ismember = False
								guess = 'in oclc but not member'
								
							# pcc?
							for this042 in field042:
								if this042.lower().strip() == 'pcc':
									pcc = '042 pcc'
								else:
									pcc = 'no'
							
							# lc copy?
							if field050_ind1 == True and field050_ind2 == True:
								lc = '050 00'
							else:
								lc = 'no'
						else:
							guess = 'not found in oclc'
								
					time.sleep(randomsnooze) # naps are healthy
				else:
					isbn = 'none'
				# insert or update db
				with con:
					cur = con.cursor() 
					if cached_date is None:
						# insert new item into db
						newitem = (itemid, lang, guess, isbn, elvi, lit, oclcnum, todaydb, callno, pcc, lc)
						cur.executemany("INSERT INTO items VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (newitem,))
					else:
						# or, if it was in the cache for a while, update the item
						updateitem = (lang, guess, isbn, elvi, lit, oclcnum, todaydb, callno, pcc, lc, itemid)
						cur.executemany("UPDATE items SET lang=?, guess=?, isbn=?, elvi=?, lit=?, oclcnum=?, date=?, callno=?, pcc=?, lc_copy=? WHERE item_id=?", (updateitem,))
					
			elif cached_date is not None and datediff < maxage:
				# just get out existing fields for new csv file
				cur.execute("SELECT * FROM items WHERE item_id=?",(itemid,))
				rows = cur.fetchall()
				for row in rows:
					elvi = str(row['elvi'])
					#lit = str(row['lit']) # probably not needed for output
					lang = str(row['lang'])
					oclcnum = str(row['oclcnum'])
					guess = str(row['guess'])
					#callno = str(row['callno'])
					callno = str(row['callno'].replace(u'\s\u2021\s','').encode('utf-8'))
					lc = str(row['lc_copy'])
					pcc = str(row['pcc'])

			#print('>>>>>>>>>>', guess)
			
			# write results of query to the new csv. NOTE: the ="" is to get around Excel's number formatting issues.
			row = (lang,field040b,itemid, bibid, '="'+isbn+'"', '="'+oclcnum+'"', elvi, ti, callno, loc, created, lc, pcc)
			if hold == 'latin_american': 
				# extra fields for latin_american
				r = list(row)
				r.append(place)
				r.append(vendor)
				r = tuple(r)
				row = r
				# we want the *full* report, member or not, for latin_american only...
				with open(outfile,'ab+') as out:
						writer = csv.writer(out)
						writer.writerow(row)
			elif hold == 'greek' and elvi != ' ':
				# we want full report (member or not) with encoding level other than full #TODO refactor
				with open(outfile,'ab+') as out:
					writer = csv.writer(out)
					writer.writerow(row)
			else: 
				# ...for all other holds, just report likely member copy...
				if guess == 'member':
					# ...and for Arabic, just certain encoding levels...
					if ((hold != 'arabic') or (hold == 'arabic' and elvi in ['I','L','4',' '])):
						with open(outfile,'ab+') as out:
							writer = csv.writer(out)
							writer.writerow(row)

	if con:
		con.close()
		

def write_summaries(hold):
	"""
	Get count of lines in each report for sparklines
	"""
	header = ["Date","Count","Hold"]
	found = False
	with open(CSVOUT + hold + '.csv') as holdreport:
		h = csv.reader(holdreport,delimiter=',', quotechar='"')
		row_count = sum(1 for row in h)

	with open(SUMMARIES+hold+'.csv','ab+') as sums:
		reader = csv.reader(sums) 
		writer = csv.writer(sums)
		newrow = [today, str(row_count-1),hold]
		for line in reader:
			if line == newrow:
				found = True
		if found == False:
			writer.writerow(newrow)
			logging.info("wrote new summary")
		else:
			logging.info("no new summary needed")


def make_html():
	"""
	generate reports.html
	"""
	htmlfile = open(HTMLOUT + 'reports.html','wb+')	
	header = """<!DOCTYPE HTML>\n<html>
	<head>
	  <meta charset="utf-8">
  <meta http-equiv="X-UA-Compatible" content="IE=edge,chrome=1">
  <title>Low-hanging fruits</title>
  <meta name="description" content="Hold stuff">
  <link rel="stylesheet" href="css/bootstrap.min.css">
  <script src="https://ajax.googleapis.com/ajax/libs/jquery/1.11.3/jquery.min.js"></script>
  <script src="http://maxcdn.bootstrapcdn.com/bootstrap/3.3.5/js/bootstrap.min.js"></script>
  <style>
  img.thumb {
	   border:1px solid #ccc;
  }
  ul {
	 list-style-type:none; 
  }
	.sparkline {
  fill: none;
  stroke: #666;
  stroke-width: 0.5px;
}
.sparkcircle {
  fill: #FF5050;
  stroke: none;
}
  body { 
  padding-top: 70px; 
}
  </style>
	</head>"""
	
	
	htmlfile.write(header)
	
	body = """
	<body>
	
	<div class="container">
	<!-- sparklines code STOLEN! from here http://www.tnoda.com/blog/2013-12-19 -->
	<div class="container">
	 <!-- Static navbar -->
      <nav class="navbar navbar-default navbar-fixed-top">
        <div class="container">
          <div class="navbar-header">
         
            <a class="navbar-brand" href="index.html">The Holds</a>
          </div>
          <div id="navbar" class="navbar-collapse collapse">
            <ul class="nav navbar-nav">
              <li><a href="reports.html">Picklists</a></li>
              <li class="dropdown">
                <a href="#" class="dropdown-toggle" data-toggle="dropdown" role="button" aria-haspopup="true" aria-expanded="false">Stats <span class="caret"></span></a>
                <ul class="dropdown-menu">
			      <li class="dropdown-header">Weekly</li>
                  <li><a href="treemap.html">Treemap</a></li>
                  <li role="separator" class="divider"></li>
                  <li class="dropdown-header">Quarterly</li>
                  <li><a href="multiples.html">Small multiples</a></li>
                  <li><a href="stacked.html">Stacked area chart</a></li>
                </ul>
              </li>
            </ul>
          </div><!--/.nav-collapse -->
        </div><!--/.container-fluid -->
      </nav>
      
	<p><img src='images/apple-web.jpg' alt='fresh apple' width="25px" height="40px" style="margin-right:10px;"/> Low-hanging fruit, ripe for the picking. Freshness date: %s. <a href="about.html">about</a></p>""" % justnow
	
	body += """
	<p><a href="./data/arabic.csv">Arabic</a> <span id="spark_ara"></span></p>
	<p><a href="./data/art.csv">Art</a> <span id="spark_art"></span></p>
	<p><a href="./data/cjk_art.csv">CJK Art</a> <span id="spark_cjk_art"></span></p>
	<p><a href="./data/cyrillic.csv">Cyrillic</a> <span id="spark_cyr"></span></p>
	<p><a href="./data/greek.csv">Greek</a> <span id="spark_gre"></span></p>
	<p><a href="./data/hebrew.csv">Hebrew</a> <span id="spark_heb"></span></p>
	<p><a href="./data/latin_american.csv">Latin American</a> <span id="spark_spa"></span></p>
	<p><a href="./data/persian.csv">Persian</a> <span id="spark_per"></span></p>
	<p><a href="./data/roman.csv">Roman</a> <span id="spark_roman"></span></p>
	<p><a href="./data/turkish.csv">Turkish</a> <span id="spark_tur"></span></p>
	<p><a href="https://docs.google.com/a/princeton.edu/forms/d/18SPb-XvSLPRxt5O2XIW4qhJpFivg9v_84wmu6B1RNUw/viewform" target="_BLANK">Custom report</a> (Google sign-in)</p>
	<sub>Sparklines (<span id="spark_sample"></span>) indicate trends in these reports since 09/01/15. For the bigger picture, look under Stats.</sub>
	</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/3.5.5/d3.min.js"></script>
<script>
var width = 40;
var height = 10;
var x = d3.scale.linear().range([0, width - 2]);
var y = d3.scale.linear().range([height - 4, 0]);
var parseDate = d3.time.format("%Y%m%d").parse;
var line = d3.svg.line()
             .interpolate("basis")
             .x(function(d) { return x(d.date); })
             .y(function(d) { return y(d.count); });

function sparkline(elemId, data) {
  data.forEach(function(d) {
    d.date = parseDate(d.Date);
    d.count = +d.Count;
  });
  x.domain(d3.extent(data, function(d) { return d.date; }));
  y.domain(d3.extent(data, function(d) { return d.count; }));

  var svg = d3.select(elemId)
              .append('svg')
              .attr('width', width)
              .attr('height', height)
              .append('g')
              .attr('transform', 'translate(0, 2)');
  svg.append('path')
     .datum(data)
     .attr('class', 'sparkline')
     .attr('d', line);
  svg.append('circle')
     .attr('class', 'sparkcircle')
     .attr('cx', x(data[data.length - 1].date))
     .attr('cy', y(data[data.length - 1].count))
     .attr('r', 1.5);  
}

d3.csv('./summaries/roman.csv', function(error, data) {
  sparkline('#spark_roman', data);
});
d3.csv('./summaries/latin_american.csv', function(error, data) {
  sparkline('#spark_spa', data);
});
d3.csv('./summaries/arabic.csv', function(error, data) {
  sparkline('#spark_ara', data);
});
d3.csv('./summaries/turkish.csv', function(error, data) {
  sparkline('#spark_tur', data);
});
d3.csv('./summaries/cyrillic.csv', function(error, data) {
  sparkline('#spark_cyr', data);
});
d3.csv('./summaries/persian.csv', function(error, data) {
  sparkline('#spark_per', data);
});
d3.csv('./summaries/greek.csv', function(error, data) {
  sparkline('#spark_gre', data);
});
d3.csv('./summaries/cjk_art.csv', function(error, data) {
  sparkline('#spark_cjk_art', data);
});
d3.csv('./summaries/art.csv', function(error, data) {
  sparkline('#spark_art', data);
});
d3.csv('./summaries/hebrew.csv', function(error, data) {
  sparkline('#spark_heb', data);
});
d3.csv('./summaries/sample.csv', function(error, data) {
  sparkline('#spark_sample', data);
});
</script>\n</body>\n</html>
	"""
	
	htmlfile.write(body)
	
	logging.info("reports.html generated")


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Generate hold reports.')
	parser.add_argument('-q','--query',dest="query",help="Query Voyager",required=False,action='store_true')
	parser.add_argument('-p','--ping',dest="ping",help="Ping WorldCat",required=False,action='store_true')
	parser.add_argument('-a','--age',dest="maxage",help="Max days after which to re-check WorldCat",required=False, default=30)
	parser.add_argument("-v", "--verbose",required=False, default=False, dest="verbose", action="store_true", help="Print out urls for feedback as it runs.")
	args = vars(parser.parse_args())
	query = args['query']
	ping = args['ping']
	maxage = int(args['maxage'])
	verbose = args['verbose']
	
	## one at a time
	#main('roman',query,ping)
	
	## loop through all holds
	holds = ['arabic','cyrillic','greek','hebrew','latin_american','persian','roman', 'turkish','cjk_art','art']
	
	for h in holds:
		main(h, query, ping)

