#!/usr/bin/env python
#-*- coding: utf-8 -*-

"""
hold.py
Generate reports to facilitate getting stuff out of the holds.
Run like `python hold.py -qp`
Do `python hold.py -h` for (sparse) details.
from 20150819
pmg
"""

import argparse
import ConfigParser
import csv
import cx_Oracle
import logging
import pickle
import random
import re
import requests
import shelve
import time
from datetime import date, datetime, timedelta
from lxml import etree

#TODO
# fix log output (remove 'requests' output)

#TODO?
# Generate PDFs?
# jinja?
# add the 'confirm' items from member copy policy?

# config
config = ConfigParser.RawConfigParser()
config.read('./config/hold.cfg') # <= production
#config.read('./config/hold_local.cfg') # <= local for debugging
USER = config.get('vger', 'user')
PASS = config.get('vger', 'pw')
PORT = config.get('vger', 'port')
SID = config.get('vger', 'sid')
HOST = config.get('vger', 'ip')
SHELF_FILE = './db/cache.db'

TEMPFILE =config.get('local', 'temp') # out.csv
CSVOUT = config.get('local', 'csv') # data from each hold's report
HTMLOUT = config.get('local', 'html') # reports.html
SUMMARIES = config.get('local', 'summaries') # summary counts (and data for sparklines)

today = time.strftime('%Y%m%d') # name log files
justnow = time.strftime("%m/%d/%Y") # freshness date of reports

class Item(object):
	def __init__(self):
		self.value = ""
		"""The Voyager ITEM_ID"""
		self.lang = ""
		"""Language of cataloging"""
		self.member = ""
		"""Our best guess at whether it's got member copy"""
		self.isbn = ""
		"""ISBN"""
		self.elvi = ""
		"""Encoding level ldr/17-18"""
		self.lit = ""
		"""Value of 008/34-35"""
		self.oclcnum = ""
		"""OCLC number"""
		self.date = ""
		"""The date of this entry in w3cdt format YYYYMMDD"""
		# TODO: add 6xx etc.?

def main(hold, query=None, ping=None,  firstitem=0, lastitem=0):
	"""
	the engine with, a little function chain
	"""
	logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p',filename='logs/'+today+'.log',level=logging.INFO)
	# the following disables the default logging of requests.get()
	requests_log = logging.getLogger("requests")
	requests_log.addHandler(logging.NullHandler())
	requests_log.propagate = False
		
	logging.info('START ' + '-' * 20)
	logging.info('hold: '+ hold)
	
	if query:
		query_vger(hold, firstitem, lastitem)
		write_summaries(hold)
		
	if ping:
		ping_worldcat(hold)
		
	make_html()
		
	logging.info('-' * 23 + 'END')

def query_vger(hold, firstitem=0, lastitem=0):
	"""
	query vger => temp csv file
	"""
	logging.info("querying Voyager")
	langs = ""
	place = "" # only for latin_american
	parens = "" # ditto
	vendor = "" # ditto (the field name)
	vendors = "" # ditto (the joins)
	isbn = "AND BIB_TEXT.ISBN is not null" # only include null isbn for latin_american
	# NOTE: vger locs were part of the original Roman Hold query. Not using them but keeping this variable here in case. 
	locs = "'6','7','13','20','21','22','24','46','84','96','138','140','142','144','163','165','171','195','197','204','214','217','221','229','250','281','287','372','419','444','446','448','450','468','492','523'"
	
	if hold == 'roman':
		langs = "'eng','fre','ger','ita','dut','rum','lat'"
	elif hold == 'latin_american':
		langs = "'spa','por','cat'"
		#place = ", TRIM(REGEXP_REPLACE(BIB_TEXT.PUB_PLACE,':\s+\z','')) as PUB_PLACE"
		# TODO Vendor info
		place = ", SUBSTR(princetondb.GETALLBIBTAG(BIB_TEXT.BIB_ID,'008'),16,3) as PUB_PLACE"
		vendor = ", VENDOR.VENDOR_CODE"
		isbn = ""
		parens = "((("
		vendors = "INNER JOIN LINE_ITEM ON BIB_TEXT.BIB_ID = LINE_ITEM.BIB_ID) INNER JOIN PURCHASE_ORDER ON LINE_ITEM.PO_ID = PURCHASE_ORDER.PO_ID) INNER JOIN VENDOR ON PURCHASE_ORDER.VENDOR_ID = VENDOR.VENDOR_ID)"
	elif hold == 'turkish':
		langs = "'tur'"
	elif hold == 'arabic':
		langs = "'ara'"
	elif hold == 'cyrillic':
		langs = "'rus', 'aze', 'bul', 'ukr'"
		
	if firstitem > 0 or lastitem > 0:
		items = "AND ITEM_STATUS.ITEM_ID between '%s' and '%s'" % (firstitem,lastitem)
	else:
		items = ""
	
	query = """SELECT BIB_TEXT.LANGUAGE, ITEM_STATUS.ITEM_ID, BIB_TEXT.BIB_ID, REGEXP_REPLACE(BIB_TEXT.ISBN,'\s.*','') AS ISBN, SUBSTR(BIB_TEXT.TITLE_BRIEF,1,25), LOCATION.LOCATION_NAME,
		ITEM.CREATE_DATE %s%s 
		FROM 
		((((((%sBIB_TEXT 
		INNER JOIN BIB_MFHD ON BIB_TEXT.BIB_ID = BIB_MFHD.BIB_ID) %s
		INNER JOIN MFHD_MASTER ON BIB_MFHD.MFHD_ID = MFHD_MASTER.MFHD_ID) 
		INNER JOIN MFHD_ITEM ON MFHD_MASTER.MFHD_ID = MFHD_ITEM.MFHD_ID)
		INNER JOIN ITEM ON MFHD_ITEM.ITEM_ID = ITEM.ITEM_ID) 
		INNER JOIN ITEM_STATUS ON MFHD_ITEM.ITEM_ID = ITEM_STATUS.ITEM_ID) 
		INNER JOIN ITEM_STATUS_TYPE ON ITEM_STATUS.ITEM_STATUS = ITEM_STATUS_TYPE.ITEM_STATUS_TYPE) 
		INNER JOIN LOCATION ON MFHD_MASTER.LOCATION_ID = LOCATION.LOCATION_ID
		WHERE 
		BIB_TEXT.LANGUAGE IN (%s) %s
		AND ITEM_STATUS_TYPE.ITEM_STATUS_TYPE ='22'
		AND princetondb.GETBIBSUBFIELD(BIB_TEXT.BIB_ID, '902','a') is null
		AND MFHD_MASTER.NORMALIZED_CALL_NO is null %s
		ORDER BY BIB_TEXT.LANGUAGE, ITEM_STATUS.ITEM_ID""" % (place, vendor, parens, vendors, langs, isbn, items)

	DSN = cx_Oracle.makedsn(HOST,PORT,SID)	
	oradb = cx_Oracle.connect(USER,PASS,DSN)
		
	rows = oradb.cursor()
	rows.execute(query)
	r = rows.fetchall()
	rows.close()
	oradb.close()

	with open(TEMPFILE,'wb+') as outfile:
		writer = csv.writer(outfile)
		header = ['LANGUAGE', 'ITEM_ID', 'BIB_ID', 'ISBN', 'TITLE_BRIEF', 'LOCATION_NAME','CREATE_DATE']
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
	shelf = shelve.open(SHELF_FILE, protocol=pickle.HIGHEST_PROTOCOL)
	datediff = 0

	with open(TEMPFILE,'rb') as indata:
		reader = csv.reader(indata,delimiter=',', quotechar='"')
		firstline = reader.next()
		
		guess = 'not found in oclc'
		isbn = 'none'
		elvi = ''
		lang = ''
		oclcnum = ''
		lit = ''
		field050 = ''
		field090 = ''
		field6xx = ''
		
		# output a new csv file
		header = ['lang', 'item_id', 'bib_id', 'isbn', 'guess', 'oclc num', 'elvi', 'lit','title','loc','item created']
		
		if hold == 'latin_american':
			header.append('place')
			header.append('vendor') 
		
		with open(outfile,'wb+') as out:
			writer = csv.writer(out)
			writer.writerow(header)
			
		for line in reader: 
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
				
			try:
				cached = shelf[itemid]
			except:
				cached = None
				
			# get number of days since bib was last checked
			if cached is not None:
				date2 = datetime.strptime(today,'%Y%m%d')
				date1 = datetime.strptime(cached.date,'%Y%m%d')
				datediff = abs((date2 - date1).days)
			
			# check cache -- don't ping worldcat unless we have to
			if cached is not None and datediff <= 30: # if it's been checked within the past month, get data from cache
				elvi = cached.elvi
				lit = cached.lit
				lang = cached.lang
				oclcnum = cached.oclcnum
				ismember = cached.member
			elif isbn is not None and isbn != '':
				times = [1.25,1.5,1.75, 2]
				randomsnooze = random.choice(times)
				r = requests.get("http://www.worldcat.org/webservices/catalog/content/isbn/"+isbn+"?servicelevel=full&wskey="+wskey)
				print("http://www.worldcat.org/webservices/catalog/content/isbn/"+isbn+"?servicelevel=full&wskey="+wskey)
				if r.status_code == 200:
					tree = etree.fromstring(r.content)
					
					if not tree.xpath("/diagnostics"): # ...because when a record isn't found, the response code is still 200
						ldr = tree.xpath("//marcxml:leader/text()",namespaces=NS)
						field008 = tree.xpath("//marcxml:controlfield[@tag='008']/text()",namespaces=NS)
						field001 = tree.xpath("//marcxml:controlfield[@tag='001']/text()",namespaces=NS)
						field050 = tree.xpath("//marcxml:datafield[@tag='050'][@ind1=' ' or @ind1=''][@ind2=' ' or @ind2='']/marcxml:subfield/@code='a'",namespaces=NS) # 050_ _ - LC call number
						field042 = tree.xpath("//marcxml:datafield[@tag='042']/text()",namespaces=NS) # 042$a - authentication code
						field090 = tree.xpath("//marcxml:datafield/@tag='090'",namespaces=NS) # 090 - shelf location
						field6xx = tree.xpath("//marcxml:datafield/@tag='600' or @tag='610' or @tag='611' or (starts-with(@tag,'65') and not(@tag='653'))",namespaces=NS) #600, 610, 611, 65[^3] - subjects
						elvi = ldr[0][17:18]
						lit = field008[0][34:35]
						lang = field008[0][35:38]
						oclcnum = field001[0]
						# from member copy cat policy checklist, possibly include later...
						#date = field008[0]
						#place = field008[0]
						#field300
						#field245
						#field250
						#field260/264
						
						if ((field050 == True or field090 == True) and ((field6xx == True or (lit != '0' or lit != ' ')))):
							ismember = True
						else:
							guess = 'in oclc but not member'
							
				time.sleep(randomsnooze) # naps are healthy
				
			if ismember == True:
				guess = 'member'
				
			# stuff into cache...
			record = Item()
			record.value = itemid
			record.lang = lang
			record.member = ismember
			record.isbn = isbn
			record.elvi = elvi
			record.lit = lit
			record.oclcnum = oclcnum
			record.date = today
			shelf[itemid] = record
			
			# write results of query to the new csv
			row = (vlang, itemid, bibid, isbn, guess, oclcnum, elvi, lit, ti, loc, created)
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
			else: 
				# ...for all other holds, just report likely member copy...
				if ismember == True:
					with open(outfile,'ab+') as out:
						writer = csv.writer(out)
						writer.writerow(row)
				
	shelf.close()


def write_summaries(hold):
	"""
	Get count of lines in each report for sparklines
	"""
	# TODO: look into Jinja (etc.), this is hacky
	header = ["Date","Count","Hold"]
	found = False
	with open(CSVOUT + hold + '.csv') as holdreport:
		h = csv.reader(holdreport,delimiter=',', quotechar='"')
		row_count = sum(1 for row in h)

	with open(SUMMARIES+hold+'.csv','ab+') as sums:
		reader = csv.reader(sums) 
		writer = csv.writer(sums)
		newrow = [today, str(row_count),hold]
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
      
	<p>Low-hanging fruit, ripe for the picking. Freshness date: %s.</p>""" % justnow
	
	body += """
	<p><a href="./data/arabic.csv">Arabic</a> <span id="spark_ara"></span></p>
	<p><a href="./data/cyrillic.csv">Cyrillic</a> <span id="spark_cyr"></span></p>
	<p><a href="./data/latin_american.csv">Latin American</a> <span id="spark_spa"></span></p>
	<p><a href="./data/roman.csv">Roman</a> <span id="spark_roman"></span></p>
	<p><a href="./data/turkish.csv">Turkish</a> <span id="spark_tur"></span></p>
	<p><a href="https://docs.google.com/a/princeton.edu/forms/d/18SPb-XvSLPRxt5O2XIW4qhJpFivg9v_84wmu6B1RNUw/viewform" target="_BLANK">Custom report</a> (Google sign-in)</p>
	<sub>Sparklines (<span id="spark_sample"></span>) indicate trends in these reports since 8/14/15. For the bigger picture, look under Stats.</sub>
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
d3.csv('./summaries/sample.csv', function(error, data) {
  sparkline('#spark_sample', data);
});
</script>\n</body>\n</html>
	"""
	
	htmlfile.write(body)
	
	logging.info("reports.html generated")


if __name__ == "__main__":
	
	# parse cli args when running locally 
	parser = argparse.ArgumentParser(description='Generate hold reports.')
	parser.add_argument('-q','--query',dest="query",help="Query Voyager",required=False,action='store_true')
	parser.add_argument('-p','--ping',dest="ping",help="Ping WorldCat",required=False,action='store_true')
	args = vars(parser.parse_args())
	query = args['query']
	ping = args['ping']
	
	holds = ['roman', 'latin_american', 'arabic', 'turkish', 'cyrillic']
	
	#for h in holds:
	main('arabic', query, ping)
