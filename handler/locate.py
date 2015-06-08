#-*-coding:utf-8-*-

#import traceback
#import logging
#import tornado.ioloop
from tornado.web import RequestHandler
import geoip2.database

reader = geoip2.database.Reader('data/GeoLite2-Country.mmdb')

class LocateHandler(RequestHandler):
    def get(self):
        #print self.request.get_header('x-geoip-country-code')
        #print self.request.remote_request()
        #print self.get_argument('remote_ip')
        response = reader.country(self.request.remote_ip)
        print response.country.iso_code
