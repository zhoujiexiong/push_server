#-*-coding:utf-8-*-

from tornado.web import RequestHandler
import geoip2.database
import settings


class Round_Robin():
    def __init__(self,data):
        self.data = data
        self.data_rr = self.get_item()

    def cycle(self,iterable):
        saved = []
        for element in iterable:
            yield element
            saved.append(element)
        while saved:
            for element in saved:
                yield element

    def get_item(self):
        for item in self.cycle(self.data):
            yield item

    def get_next(self):
        return self.data_rr.next()
    

class LocateHandler(RequestHandler):
    reader = geoip2.database.Reader(settings.MMDB_COUNTRY)
    rr = {}
    for service in settings.CLUSTER.keys():
        rr[service] = {}
        for area in settings.CLUSTER[service].keys():
            rr[service][area] = Round_Robin(xrange(len(settings.CLUSTER[service][area])))
    
    def get(self):
        response = self.__class__.reader.country(self.request.remote_ip)
        country = response.country.iso_code
        area = settings.DEFAULT_AREA
        for k, v in settings.DISPATCH_MAP.iteritems():
            if country in v:
                area = k
                break    
        resp = {}
        for service in settings.CLUSTER.keys():
            index = self.__class__.rr[service][area].get_next()
            resp[service] = settings.CLUSTER[service][area][index]
        self.finish(resp)
            
        
