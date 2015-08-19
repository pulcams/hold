#!/usr/bin/env python
#-*- coding: utf-8 -*-

"""
Generate treemap of hold.
from 20150617
pmg
"""

import ConfigParser
import csv
import os
import time
import cx_Oracle

config = ConfigParser.RawConfigParser()
config.read('./config/hold.cfg') # <= "production"
#config.read('./config/hold_local.cfg') # <= local
user = config.get('vger', 'user')
pw = config.get('vger', 'pw')
port = config.get('vger', 'port')
sid = config.get('vger', 'sid')
ip = config.get('vger', 'ip')
csvout = config.get('local', 'csv')
htmlout = config.get('local', 'html')

dsn_tns = cx_Oracle.makedsn(ip,port,sid)
db = cx_Oracle.connect(user,pw,dsn_tns)

justnow = time.strftime("%m/%d/%Y %I:%M %p")
today = time.strftime("%Y%m%d")

def main():
	"""
	function chain
	"""
	run_query()
	make_html()

def run_query():
	"""
	query Voyager for summaries
	"""
	q = """SELECT BIB_TEXT.LANGUAGE, Count(BIB_TEXT.BIB_ID) AS total,
MAX(princetondb.GETBIBSUBFIELD(BIB_TEXT.BIB_ID, '904','e')) as max904, MIN(princetondb.GETBIBSUBFIELD(BIB_TEXT.BIB_ID, '904','e')) as min904,
MIN(to_char(ITEM.CREATE_DATE,'yyyymmdd')) as mincre,
to_char(SYSDATE,'yyyymmdd') as today, to_date(to_char(SYSDATE,'yyyymmdd'),'yyyymmdd') -  MIN(to_date(to_char(ITEM.CREATE_DATE,'yyyymmdd'),'yyyymmdd')) as max_age_in_days
FROM
(((((BIB_TEXT 
INNER JOIN BIB_MFHD ON BIB_TEXT.BIB_ID = BIB_MFHD.BIB_ID
INNER JOIN MFHD_MASTER ON BIB_MFHD.MFHD_ID = MFHD_MASTER.MFHD_ID) 
INNER JOIN MFHD_ITEM ON MFHD_MASTER.MFHD_ID = MFHD_ITEM.MFHD_ID)
INNER JOIN ITEM ON MFHD_ITEM.ITEM_ID = ITEM.ITEM_ID) 
INNER JOIN ITEM_STATUS ON MFHD_ITEM.ITEM_ID = ITEM_STATUS.ITEM_ID) 
INNER JOIN ITEM_STATUS_TYPE ON ITEM_STATUS.ITEM_STATUS = ITEM_STATUS_TYPE.ITEM_STATUS_TYPE) 
INNER JOIN LOCATION ON MFHD_MASTER.LOCATION_ID = LOCATION.LOCATION_ID
WHERE
BIB_TEXT.LANGUAGE IN ('spa','por','cat','rus','ukr','bul','srp','chu','chv','tat','aze','bel','tur','ara','eng','fre','ger','ita','dut','rum','lat') 
AND ITEM_STATUS_TYPE.ITEM_STATUS_TYPE ='22'
AND princetondb.GETBIBSUBFIELD(BIB_TEXT.BIB_ID, '902','a') is null
AND MFHD_MASTER.NORMALIZED_CALL_NO is null
GROUP BY  BIB_TEXT.LANGUAGE"""
	
	c = db.cursor()
	c.execute(q)
	with open(csvout+'output_'+today+'.csv','wb+') as outfile:
		writer = csv.writer(outfile)
		header = ['lang','total','max904','min904','mincre','today','max_age_in_days']
		writer.writerow(header) 
		for row in c:
			writer.writerow(row)
	c.close()

def make_html():
	"""
	make treemap.html
	"""
	reader = csv.reader(open(csvout+'output_'+today+'.csv'))
	htmlfile = open(htmlout+'treemap.html','wb+')
	rownum = 0
	header = """<!DOCTYPE html>\n<html>
	<head>
	<meta charset="utf-8" />
	<script src="http://www.d3plus.org/js/d3.js"></script>
	<script src="http://www.d3plus.org/js/d3plus.js"></script>
	<link rel="stylesheet" href="css/style.css">
	<link rel="stylesheet" href="css/bootstrap.min.css"> 
	<script src="https://ajax.googleapis.com/ajax/libs/jquery/1.11.3/jquery.min.js"></script>
	<script src="http://maxcdn.bootstrapcdn.com/bootstrap/3.3.5/js/bootstrap.min.js"></script>
	</head>
	<body>
	<div id="container">
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
      <p style="margin-left:60px;">An educated guess about the number of items in the hold this week, based on language groups. Last updated """+justnow+""".</p>
    </div>
	<div id="viz" class="container" style="width:60%;height:600px;"></div>
	<script>
    var hold_data = [
    """
	
	footer = """
	];
  // instantiate d3plus
  var visualization = d3plus.viz()
    .container("#viz")
    .data(hold_data)
    .type("tree_map")
    .id(["group","name"])
    .size("value")
    .font("serif")
    .color("oldest")
    .zoom(true)
    .depth(0)
    .ui([
       {
        "method" : "depth",
        "type": "drop",
        "value"  : [{"by language": 1}, {"by hold": 0}]
      },
      {
        "method" : "color",
        "type": "drop",
        "value"  : [{"by hold": "group"}, {"by max age": "oldest"}]
      }
    ])
    .draw()
 
</script>"""
	rownum = 0
	group = ''
	htmlfile.write(header)
	for row in reader:
		lang = row[0]
		count = row[1]
		age = row[6]
		if lang == 'ara':
			group = "Arabic"
		elif lang == 'tur':
			group = "Turkish"
		elif lang in ('spa','por','cat'):
			group = "Latin American"
		elif lang in ('rus','ukr','bul','srp','chu','chv','tat','aze','bel'):
			group = "Cyrillic"
		elif lang in ('eng','fre','ger','ita','dut','rum','lat'):
			group = 'Roman'
			
		if rownum == 1:
			htmlfile.write('{"value":%s,"name": "%s", "group": "%s","oldest":%s}' % (count,lang,group,age))
		elif rownum > 1:
			htmlfile.write(',\n{"value":%s,"name": "%s", "group": "%s","oldest":%s}' % (count,lang,group,age))
		rownum += 1
	htmlfile.write(footer)

if __name__ == "__main__":
	main()

