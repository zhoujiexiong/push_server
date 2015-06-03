#-*- coding:utf-8 -*-

import redis
import json

r = redis.Redis()

msg = {}
#msg['to'] = '00004CE1BB000072'
msg['to'] = '00004CE1BB000036'
msg['from'] = 'zjx'
msg['type'] = 'message'
print json.dumps(msg)
#print r.publish('forward', json.dumps(msg))
print r.publish('test', json.dumps(msg))
